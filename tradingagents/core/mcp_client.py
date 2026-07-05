"""HTTP MCP client for the local StockManager service.

TradingAgents treats StockManager as an independently managed localhost
service.  The client performs a lightweight REST health/capability probe, then
uses MCP Streamable HTTP for tool calls.  Failures are intentionally soft so the
rest of the app can degrade to local data sources.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable
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
        # A semaphore (not a lock) so multiple MCP tool calls can run concurrently
        # — RiskMonitor scanning N holdings would otherwise serialize and a
        # single 120s timeout would block every subsequent call.
        self._call_semaphore = asyncio.Semaphore(8)
        self._status = MCPStatus(enabled=self._config.enabled, url=self._config.url)

    async def __aenter__(self) -> "StockManagerMCPClient":
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
        if self._connected:
            return True
        # Clean up any leftover session/transport from a prior failed call so the
        # next connection attempt starts from a clean slate. We only mark
        # ``_connected = False`` on failure (never disconnect while holding the
        # call lock), so dangling resources may exist here.
        if self._session is not None or self._transport_context is not None:
            await self.disconnect()
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
            logger.info("Connected to StockManager MCP Server at %s", self._config.url)
            return True
        except Exception as exc:
            self._status.error = f"{type(exc).__name__}: {exc}"
            logger.warning("Failed to connect to StockManager MCP: %s", exc)
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        """Close the MCP session."""
        if self._session_context is not None:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception:
                pass
        if self._transport_context is not None:
            try:
                await self._transport_context.__aexit__(None, None, None)
            except Exception:
                pass
        self._session_context = None
        self._transport_context = None
        self._session = None
        self._read = None
        self._write = None
        self._get_session_id = None
        self._connected = False
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
        if not await self.connect():
            logger.warning("MCP not connected; skipping %s", name)
            return None
        async with self._call_semaphore:
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(name, arguments),
                    timeout=self._config.tool_timeout,
                )
                if result.content:
                    text = getattr(result.content[0], "text", str(result.content[0]))
                    try:
                        return json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        return text
                return None
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

    async def rank_factor_candidates(
        self,
        universe_index: str,
        trade_date: str,
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
        return await self._call_tool("rank_factor_candidates", args)

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
