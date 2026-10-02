"""HTTP MCP client for the local StockManager service.

TradingAgents treats StockManager as an independently managed localhost
service.  The client performs a lightweight REST health/capability probe, then
uses MCP Streamable HTTP for tool calls.  Failures are intentionally soft so the
rest of the app can degrade to local data sources.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

DEFAULT_STOCKMANAGER_MCP_URL = "http://127.0.0.1:8765/mcp"


@dataclass
class MCPConfig:
    """Connection parameters for StockManager MCP Server."""

    url: str = DEFAULT_STOCKMANAGER_MCP_URL
    enabled: bool = True
    tool_timeout: float = 120.0
    sse_read_timeout: float = 300.0
    health_timeout: float = 2.0
    # Total budget for waiting on an async MCP job (rank_factor_candidates
    # with async_mode); individual status polls still use tool_timeout.
    job_poll_timeout: float = 600.0


@dataclass
class MCPStatus:
    """Current StockManager MCP availability and capability snapshot."""

    enabled: bool = True
    connected: bool = False
    url: str = DEFAULT_STOCKMANAGER_MCP_URL
    health: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    tools: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def capability_flags(self) -> dict[str, bool]:
        caps = self.capabilities or {}
        return {
            "market_data_available": bool(caps.get("market_data_available")),
            "factor_available": bool(caps.get("factor_available")),
            "backtest_available": bool(caps.get("backtest_available")),
            "trading_plan_available": bool(caps.get("trading_plan_available")),
            "risk_announcement_available": bool(caps.get("risk_announcement_available")),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "url": self.url,
            "health": self.health,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "capability_flags": self.capability_flags,
            "error": self.error,
        }


def config_from_app_config(config: dict[str, Any] | None) -> MCPConfig:
    """Build MCPConfig from the shared TradingAgents config dict."""
    config = config or {}
    return MCPConfig(
        url=str(config.get("stockmanager_mcp_url") or DEFAULT_STOCKMANAGER_MCP_URL),
        enabled=bool(config.get("stockmanager_mcp_enabled", True)),
        tool_timeout=float(config.get("stockmanager_mcp_timeout", 30.0)),
        sse_read_timeout=float(config.get("stockmanager_mcp_sse_read_timeout", 300.0)),
        health_timeout=float(config.get("stockmanager_mcp_health_timeout", 2.0)),
        job_poll_timeout=float(config.get("stockmanager_mcp_job_poll_timeout", 600.0)),
    )


class StockManagerMCPClient:
    """Async wrapper around a StockManager Streamable HTTP MCP session."""

    def __init__(self, config: MCPConfig | None = None):
        self._config = config or MCPConfig()
        self._session: Any = None
        self._transport_context: Any = None
        self._session_context: Any = None
        self._read: Any = None
        self._write: Any = None
        self._get_session_id: Callable[[], str | None] | None = None
        self._connected = False
        self._session_owner_task: asyncio.Task[Any] | None = None
        # A semaphore (not a lock) so multiple MCP tool calls can run concurrently
        # — RiskMonitor scanning N holdings would otherwise serialize and a
        # single 120s timeout would block every subsequent call.
        self._call_semaphore = asyncio.Semaphore(8)
        self._connection_lock = asyncio.Lock()
        self._active_calls = 0
        self._idle_calls = asyncio.Event()
        self._idle_calls.set()
        self._status = MCPStatus(enabled=self._config.enabled, url=self._config.url)

    async def __aenter__(self) -> StockManagerMCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def status(self) -> MCPStatus:
        return self._status

    def supports_tool_feature(self, tool: str, feature: str) -> bool:
        """Check the server's capabilities.tool_features declaration.

        Requirements doc R4: consumers must feature-detect optional behaviors
        (async_mode, batch_ts_codes, ...) instead of hardcoding versions.
        """
        caps = self._status.capabilities or {}
        features = caps.get("tool_features") or {}
        tool_features = features.get(tool)
        return isinstance(tool_features, list) and feature in tool_features

    async def refresh_status(self) -> MCPStatus:
        """Refresh REST health and capability status without opening MCP tools."""
        self._status = MCPStatus(enabled=self._config.enabled, url=self._config.url)
        if not self._config.enabled:
            self._status.error = "StockManager MCP integration disabled"
            return self._status

        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=self._config.health_timeout,
                trust_env=False,
            ) as client:
                health_resp = await client.get(_sibling_url(self._config.url, "/health"))
                health_resp.raise_for_status()
                caps_resp = await client.get(_sibling_url(self._config.url, "/capabilities"))
                caps_resp.raise_for_status()
                health = health_resp.json()
                capabilities = caps_resp.json()

            self._status.health = health
            self._status.capabilities = capabilities
            self._status.tools = sorted(capabilities.get("tools") or [])
            self._status.connected = self._connected
        except Exception as exc:
            self._status.error = f"{type(exc).__name__}: {exc}"
            logger.info("StockManager MCP status probe failed: %s", exc)
        return self._status

    async def connect(self) -> bool:
        """Establish the MCP Streamable HTTP session. Returns True on success."""
        if (
            not self._connected
            and (self._session is not None or self._transport_context is not None)
        ):
            await self.disconnect()
        async with self._connection_lock:
            if self._connected:
                return True
            # A failed generation is not torn down while another tool call is
            # still leasing it. This prevents reconnect from invalidating the
            # shared session underneath concurrent callers.
            await self._idle_calls.wait()
            return await self._connect_unlocked()

    async def _connect_unlocked(self) -> bool:
        # Clean up any leftover session/transport from a prior failed call so the
        # next connection attempt starts from a clean slate. We only mark
        # ``_connected = False`` on failure (never disconnect while holding the
        # call lock), so dangling resources may exist here.
        if self._session is not None or self._transport_context is not None:
            await self._disconnect_unlocked()
        status = await self.refresh_status()
        if not self._config.enabled:
            return False
        if status.health is None:
            return False

        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            self._transport_context = streamablehttp_client(
                self._config.url,
                timeout=self._config.tool_timeout,
                sse_read_timeout=self._config.sse_read_timeout,
            )
            self._read, self._write, self._get_session_id = await asyncio.wait_for(
                self._transport_context.__aenter__(),
                timeout=self._config.tool_timeout,
            )
            self._session_context = ClientSession(self._read, self._write)
            self._session = await self._session_context.__aenter__()
            await self._session.initialize()

            tools_result = await asyncio.wait_for(
                self._session.list_tools(),
                timeout=self._config.tool_timeout,
            )
            self._status.tools = sorted(t.name for t in tools_result.tools)
            self._status.connected = True
            self._status.error = None
            self._connected = True
            self._session_owner_task = asyncio.current_task()
            logger.info("Connected to StockManager MCP Server at %s", self._config.url)
            return True
        except Exception as exc:
            self._status.error = f"{type(exc).__name__}: {exc}"
            logger.warning("Failed to connect to StockManager MCP: %s", exc)
            await self._disconnect_unlocked()
            return False

    async def disconnect(self) -> None:
        """Close the MCP session."""
        async with self._connection_lock:
            await self._idle_calls.wait()
            await self._disconnect_unlocked()

    async def _acquire_session_lease(self) -> bool:
        """Connect and increment the active generation lease atomically."""
        async with self._connection_lock:
            if not self._connected:
                await self._idle_calls.wait()
                if not await self._connect_unlocked():
                    return False
            self._active_calls += 1
            self._idle_calls.clear()
            return True

    async def _disconnect_unlocked(self) -> None:
        """Close resources while the connection lock is held."""
        if self._session_context is not None:
            with contextlib.suppress(Exception):
                await self._session_context.__aexit__(None, None, None)
        if self._transport_context is not None:
            with contextlib.suppress(Exception):
                await self._transport_context.__aexit__(None, None, None)
        self._session_context = None
        self._transport_context = None
        self._session = None
        self._read = None
        self._write = None
        self._get_session_id = None
        self._connected = False
        self._session_owner_task = None
        self._status.connected = False

    async def list_tools(self) -> list[str]:
        if not await self.connect():
            return []
        try:
            result = await asyncio.wait_for(
                self._session.list_tools(),
                timeout=self._config.tool_timeout,
            )
            self._status.tools = sorted(t.name for t in result.tools)
            return self._status.tools
        except Exception as exc:
            logger.warning("MCP list_tools failed: %s", exc)
            return []

    async def _call_tool(self, name: str, arguments: dict) -> Any:
        """Call an MCP tool and return parsed JSON, or None on failure."""
        async with self._call_semaphore:
            # AnyIO's Streamable HTTP transport owns a cancel scope tied to the
            # task that entered it. FastAPI startup and run tasks are different
            # tasks, so reusing the startup session here eventually raises
            # "Attempted to exit cancel scope in a different task". Use a
            # one-shot session when crossing that task boundary; entry, call
            # and exit then happen in the same task.
            owner = self._session_owner_task
            if self._connected and owner is not None and owner is not asyncio.current_task():
                return await self._call_tool_isolated(name, arguments)
            if not await self._acquire_session_lease():
                logger.warning("MCP not connected; skipping %s", name)
                return None
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(name, arguments),
                    timeout=self._config.tool_timeout,
                )
                return self._decode_tool_result(name, result)
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP tool %s timed out after %.0fs (increase stockmanager_mcp_timeout if needed)",
                    name,
                    self._config.tool_timeout,
                )
                # A timeout usually means the session is stuck; mark disconnected so
                # the next call re-establishes the session rather than reusing it.
                self._connected = False
                return None
            except Exception as exc:
                logger.warning("MCP tool %s failed: %s", name, exc)
                # Mark disconnected so the next call triggers reconnect. We do not
                # disconnect() here because we hold the call semaphore; connect()
                # will clean up dangling resources before rebuilding.
                self._connected = False
                return None
            finally:
                self._active_calls -= 1
                if self._active_calls == 0:
                    self._idle_calls.set()

    async def _call_tool_isolated(self, name: str, arguments: dict) -> Any:
        """Call through a task-local transport to respect AnyIO ownership."""

        async def invoke() -> Any:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async with (
                streamablehttp_client(
                    self._config.url,
                    timeout=self._config.tool_timeout,
                    sse_read_timeout=self._config.sse_read_timeout,
                ) as (read, write, _get_session_id),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(name, arguments)
                return self._decode_tool_result(name, result)

        try:
            return await asyncio.wait_for(invoke(), timeout=self._config.tool_timeout)
        except asyncio.TimeoutError:
            message = (
                f"MCP tool {name} timed out after {self._config.tool_timeout:.0f}s"
            )
            self._status.error = message
            logger.warning(message)
            return None
        except Exception as exc:
            self._status.error = f"{type(exc).__name__}: {exc}"
            logger.warning("MCP isolated tool %s failed: %s", name, exc)
            return None

    @staticmethod
    def _decode_tool_result(name: str, result: Any) -> Any:
        """Decode the common MCP content envelope into JSON-compatible data."""
        if not result.content:
            return None
        raw_text = getattr(result.content[0], "text", str(result.content[0]))
        if bool(getattr(result, "isError", False)):
            return {
                "status": "error",
                "error": {"code": "mcp_tool_error", "message": str(raw_text)},
                "rows": [],
                "warnings": [f"StockManager tool {name} returned an error"],
            }
        try:
            payload = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            return {
                "status": "error",
                "error": {
                    "code": "invalid_json",
                    "message": f"Tool {name} returned non-JSON content",
                },
                "rows": [],
                "warnings": [str(raw_text)[:500]],
            }
        if not isinstance(payload, (dict, list)):
            return {
                "status": "error",
                "error": {
                    "code": "invalid_payload_type",
                    "message": f"Tool {name} returned {type(payload).__name__}",
                },
                "rows": [],
                "warnings": [],
            }
        return payload

    # Data Query Tools

    async def get_stock_daily(
        self, ts_codes: list[str], start_date: str, end_date: str, adj_type: str = "qfq"
    ) -> dict | None:
        return await self._call_tool(
            "get_stock_daily",
            {"ts_codes": ts_codes, "start_date": start_date, "end_date": end_date, "adj_type": adj_type},
        )

    async def get_index_daily(self, index_code: str, start_date: str, end_date: str) -> dict | None:
        return await self._call_tool(
            "get_index_daily",
            {"index_code": index_code, "start_date": start_date, "end_date": end_date},
        )

    async def get_index_constituents(self, index_code: str, trade_date: str) -> dict | None:
        return await self._call_tool(
            "get_index_constituents",
            {"index_code": index_code, "trade_date": trade_date},
        )

    async def get_trading_calendar(self, index_code: str, start_date: str, end_date: str) -> dict | None:
        return await self._call_tool(
            "get_trading_calendar",
            {"index_code": index_code, "start_date": start_date, "end_date": end_date},
        )

    async def get_institutional_flow(self, flow_type: str, start_date: str, end_date: str) -> dict | None:
        return await self._call_tool(
            "get_institutional_flow",
            {"flow_type": flow_type, "start_date": start_date, "end_date": end_date},
        )

    async def get_financial_metrics(self, ts_code: str, end_date: str) -> dict | None:
        return await self._call_tool(
            "get_financial_metrics",
            {"ts_code": ts_code, "end_date": end_date},
        )

    async def get_industry_map(self, ts_codes: list[str] | None = None) -> dict | None:
        args: dict = {}
        if ts_codes:
            args["ts_codes"] = ts_codes
        return await self._call_tool("get_industry_map", args)

    async def get_risk_announcements(
        self, ts_code: str, start_date: str, end_date: str, keywords: list[str] | None = None
    ) -> dict | None:
        args = {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}
        if keywords:
            args["keywords"] = keywords
        return await self._call_tool("get_risk_announcements", args)

    async def get_risk_announcements_batch(
        self,
        ts_codes: list[str],
        start_date: str,
        end_date: str,
        keywords: list[str] | None = None,
    ) -> dict | None:
        """Batch risk-announcement scan (requirements doc R3).

        Returns None when the server does not declare ``batch_ts_codes`` so
        callers can fall back to the per-symbol loop. Rows come back flat with
        a ``ts_code`` field on each row (``flat_rows`` feature).
        """
        if not ts_codes:
            return {"status": "success", "rows": [], "warnings": []}
        if not await self.connect():
            return None
        if not self.supports_tool_feature("get_risk_announcements", "batch_ts_codes"):
            return None
        args: dict[str, Any] = {
            "ts_codes": ts_codes,
            "start_date": start_date,
            "end_date": end_date,
        }
        if keywords:
            args["keywords"] = keywords
        return await self._call_tool("get_risk_announcements", args)

    async def get_factor_snapshot(
        self,
        ts_codes: list[str],
        trade_date: str,
        lookback_days: int = 120,
        factors: list[str] | None = None,
        adj_type: str = "qfq",
        include_raw: bool = True,
        include_percentiles: bool = True,
    ) -> dict | None:
        args: dict[str, Any] = {
            "ts_codes": ts_codes,
            "trade_date": trade_date,
            "lookback_days": lookback_days,
            "adj_type": adj_type,
            "include_raw": include_raw,
            "include_percentiles": include_percentiles,
        }
        if factors:
            args["factors"] = factors
        return await self._call_tool("get_factor_snapshot", args)

    async def get_trade_review_snapshot(
        self,
        ts_code: str,
        trade_date: str,
        lookback_days: int = 120,
        adj_type: str = "qfq",
        plan: dict[str, Any] | None = None,
    ) -> dict | None:
        """Fetch the MCP-owned point-in-time chart and deterministic execution gate."""
        args: dict[str, Any] = {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "lookback_days": lookback_days,
            "adj_type": adj_type,
        }
        if plan:
            args["plan"] = plan
        return await self._call_tool("get_trade_review_snapshot", args)

    async def get_chip_distribution_snapshot(
        self,
        ts_code: str,
        trade_date: str,
        lookback_days: int = 120,
        adj_type: str = "qfq",
    ) -> dict | None:
        """Fetch MCP-owned point-in-time chip-cost distribution evidence."""
        return await self._call_tool(
            "get_chip_distribution_snapshot",
            {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "lookback_days": lookback_days,
                "adj_type": adj_type,
            },
        )

    async def rank_factor_candidates(
        self,
        universe_index: str,
        trade_date: str,
        universe_indices: list[str] | None = None,
        style: str = "medium_term",
        limit: int = 20,
        candidate_limit: int = 200,
        factor_profile: str | None = None,
        weights: dict[str, float] | None = None,
        filters: dict[str, Any] | None = None,
        sector_prefs: list[str] | None = None,
        return_factor_snapshot: bool = True,
        enable_decision: bool = True,
        max_per_industry: int | None = 3,
        decision_config: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict | None:
        args: dict[str, Any] = {
            "universe_index": universe_index,
            "trade_date": trade_date,
            "style": style,
            "limit": limit,
            "candidate_limit": candidate_limit,
            "return_factor_snapshot": return_factor_snapshot,
            "enable_decision": enable_decision,
        }
        if universe_indices:
            args["universe_indices"] = list(dict.fromkeys(universe_indices))
        if max_per_industry is not None:
            args["max_per_industry"] = max_per_industry
        if decision_config:
            args["decision_config"] = decision_config
        if factor_profile:
            args["factor_profile"] = factor_profile
        if weights:
            args["weights"] = weights
        if filters:
            args["filters"] = filters
        if sector_prefs:
            args["sector_prefs"] = sector_prefs
        # Requirements doc R1: prefer the async job path when the server
        # declares it — submit returns immediately, then status polls surface
        # progress (stage/pct) so skills can stream it to the UI timeline.
        if not await self.connect():
            logger.warning("MCP not connected; skipping rank_factor_candidates")
            return _rank_transport_error(
                "mcp_unavailable",
                self._status.error or "StockManager MCP connection unavailable",
            )
        if not self.supports_tool_feature("rank_factor_candidates", "async_mode"):
            payload = await self._call_tool("rank_factor_candidates", args)
            return payload if payload is not None else _rank_transport_error(
                "mcp_transport_failure",
                self._status.error or "StockManager ranking call returned no response",
            )
        submit = await self._call_tool(
            "rank_factor_candidates", {**args, "async_mode": True}
        )
        if submit is None:
            return _rank_transport_error(
                "mcp_transport_failure",
                self._status.error or "StockManager ranking submission returned no response",
            )
        job_id = submit.get("job_id") if isinstance(submit, dict) else None
        if not job_id:
            # Server ignored async_mode (or errored): treat as a sync payload.
            return submit
        return await self._wait_rank_job(str(job_id), progress_callback)

    async def _wait_rank_job(
        self,
        job_id: str,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> dict | None:
        """Poll an async ranking job to completion, forwarding progress updates."""
        started = time.monotonic()
        last_progress: dict[str, Any] | None = None
        while True:
            status_payload = await self._call_tool("get_job_status", {"job_id": job_id})
            if not isinstance(status_payload, dict):
                return _rank_transport_error(
                    "mcp_job_status_unavailable",
                    self._status.error or f"StockManager ranking job {job_id} status unavailable",
                )
            job_status = str(status_payload.get("status") or "").lower()
            progress = status_payload.get("progress")
            if progress_callback and isinstance(progress, dict) and progress != last_progress:
                last_progress = progress
                try:
                    await progress_callback(progress)
                except Exception as exc:
                    logger.debug("rank job progress callback failed: %s", exc)
            if job_status in {"succeeded", "success", "done", "completed"}:
                result_payload = await self._call_tool("get_job_result", {"job_id": job_id})
                unwrapped = _unwrap_job_result(result_payload)
                return unwrapped if unwrapped is not None else _rank_transport_error(
                    "mcp_job_result_unavailable",
                    self._status.error or f"StockManager ranking job {job_id} result unavailable",
                )
            if job_status in {"failed", "error", "cancelled"}:
                return {
                    "status": "error",
                    "error": {
                        "code": "mcp_job_failed",
                        "message": str(status_payload.get("error") or f"job {job_id} {job_status}"),
                    },
                    "rows": [],
                    "warnings": [f"StockManager ranking job {job_id} ended as {job_status}"],
                }
            if time.monotonic() - started > self._config.job_poll_timeout:
                logger.warning(
                    "MCP ranking job %s exceeded job_poll_timeout=%.0fs",
                    job_id,
                    self._config.job_poll_timeout,
                )
                return {
                    "status": "error",
                    "error": {"code": "mcp_job_timeout", "message": f"job {job_id} timed out"},
                    "rows": [],
                    "warnings": [
                        f"StockManager ranking job {job_id} did not finish within "
                        f"{self._config.job_poll_timeout:.0f}s"
                    ],
                }
            await asyncio.sleep(1.0)

    # Experiment Tools

    async def list_available_factors(self) -> dict | None:
        return await self._call_tool("list_available_factors", {})

    async def list_strategies_and_configs(self) -> dict | None:
        return await self._call_tool("list_strategies_and_configs", {})

    async def evaluate_signal_formula(
        self, formula: str, start_date: str, end_date: str, universe_index: str = "000906.SH"
    ) -> dict | None:
        return await self._call_tool(
            "evaluate_signal_formula",
            {
                "formula": formula,
                "start_date": start_date,
                "end_date": end_date,
                "universe_index": universe_index,
            },
        )

    async def evaluate_single_factor(
        self, factor_type: str, factor_params: dict, start_date: str, end_date: str
    ) -> dict | None:
        return await self._call_tool(
            "evaluate_single_factor",
            {
                "factor_type": factor_type,
                "factor_params": factor_params,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

    async def run_backtest(
        self, strategy: str, config: str, start: str, end: str,
        initial_cash: float | None = None, universe_size: int | None = None,
    ) -> dict | None:
        args: dict = {"strategy": strategy, "config": config, "start": start, "end": end}
        if initial_cash is not None:
            args["initial_cash"] = initial_cash
        if universe_size is not None:
            args["universe_size"] = universe_size
        return await self._call_tool("run_backtest", args)

    async def run_factor_experiment(self, experiment: dict) -> dict | None:
        return await self._call_tool("run_factor_experiment", {"experiment": experiment})

    async def run_ablation_study(self, base_experiment: dict, ablations: list | None = None) -> dict | None:
        args: dict = {"base_experiment": base_experiment}
        if ablations:
            args["ablations"] = ablations
        return await self._call_tool("run_ablation_study", args)

    async def get_job_status(self, job_id: str) -> dict | None:
        return await self._call_tool("get_job_status", {"job_id": job_id})

    async def get_job_result(self, job_id: str) -> dict | None:
        return await self._call_tool("get_job_result", {"job_id": job_id})

    # Advisor Tools

    async def resolve_stock_name(self, codes: list[str]) -> dict | None:
        return await self._call_tool("resolve_stock_name", {"codes": codes})

    async def generate_trading_plan(
        self, strategy: str, config: str, as_of_date: str,
        positions: list[dict], cash: float,
    ) -> dict | None:
        return await self._call_tool(
            "generate_trading_plan",
            {
                "strategy": strategy,
                "config": config,
                "as_of_date": as_of_date,
                "positions": positions,
                "cash": cash,
            },
        )

    async def analyze_execution_slippage(
        self, trades: list[dict], participation_rates: list[float] | None = None,
    ) -> dict | None:
        args: dict = {"trades": trades}
        if participation_rates:
            args["participation_rates"] = participation_rates
        return await self._call_tool("analyze_execution_slippage", args)

    async def compute_purged_cv_sharpe(
        self, equity_curve: list[dict], n_splits: int = 5, purge_days: int = 5,
    ) -> dict | None:
        return await self._call_tool(
            "compute_purged_cv_sharpe",
            {"equity_curve": equity_curve, "n_splits": n_splits, "purge_days": purge_days},
        )


_client_instance: StockManagerMCPClient | None = None
_client_lock = asyncio.Lock()


async def get_mcp_client(config: MCPConfig | dict[str, Any] | None = None) -> StockManagerMCPClient | None:
    """Get or create the singleton MCP client; returns None when unavailable."""
    global _client_instance
    async with _client_lock:
        mcp_config = config if isinstance(config, MCPConfig) else config_from_app_config(config)
        if not mcp_config.enabled:
            if _client_instance is not None:
                await _client_instance.disconnect()
                _client_instance = None
            return None
        if _client_instance is not None and _client_instance.is_connected:
            if _client_instance.status.url == mcp_config.url:
                return _client_instance
            await _client_instance.disconnect()
            _client_instance = None

        client = StockManagerMCPClient(mcp_config)
        if await client.connect():
            _client_instance = client
            return client
        return None


async def get_mcp_status(config: MCPConfig | dict[str, Any] | None = None) -> MCPStatus:
    """Return a current status snapshot without requiring a successful MCP session."""
    global _client_instance
    if _client_instance is not None:
        await _client_instance.refresh_status()
        _client_instance.status.connected = _client_instance.is_connected
        return _client_instance.status

    mcp_config = config if isinstance(config, MCPConfig) else config_from_app_config(config)
    client = StockManagerMCPClient(mcp_config)
    return await client.refresh_status()


async def shutdown_mcp_client() -> None:
    """Disconnect the singleton MCP client."""
    global _client_instance
    async with _client_lock:
        if _client_instance is not None:
            await _client_instance.disconnect()
            _client_instance = None


def _sibling_url(url: str, path: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _unwrap_job_result(payload: Any) -> dict | None:
    """Unwrap the get_job_result envelope {job_id, status, result, warnings}.

    The inner ``result`` is the same shape as the synchronous tool payload;
    envelope-level warnings are merged so callers see them alongside the
    tool's own warnings.
    """
    if not isinstance(payload, dict):
        return payload
    inner = payload.get("result")
    if not isinstance(inner, dict):
        return payload
    envelope_warnings = [str(item) for item in payload.get("warnings") or []]
    if envelope_warnings:
        inner = {**inner}
        inner["warnings"] = [*(inner.get("warnings") or []), *envelope_warnings]
    return inner


def _rank_transport_error(code: str, message: str) -> dict[str, Any]:
    """Typed failure envelope so selection skills never confuse outages with zero rows."""
    return {
        "status": "error",
        "error": {"code": code, "message": message},
        "rows": [],
        "warnings": [message],
    }
