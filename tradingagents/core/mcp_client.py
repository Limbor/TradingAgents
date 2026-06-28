"""MCP Client for StockManager — 量化研究平台能力桥接.

通过 stdio 协议连接 StockManager MCP Server，将回测/因子/数据查询等
能力暴露为 Python async API。连接失败时降级到本地数据源。

Usage::

    from tradingagents.core.mcp_client import StockManagerMCPClient

    async with StockManagerMCPClient() as client:
        names = await client.resolve_stock_name(["600519.SH", "000858.SZ"])
        daily = await client.get_stock_daily(["600519.SH"], "2026-06-01", "2026-06-27")
        metrics = await client.get_financial_metrics("600519.SH", "2026-03-31")
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

STOCKMANAGER_DIR_DEFAULT = str(
    Path(__file__).resolve().parent.parent.parent.parent / "StockManager"
)

# If StockManager exists alongside TradingAgents, auto-detect; otherwise
# the caller must supply an explicit path.
if not os.path.isdir(STOCKMANAGER_DIR_DEFAULT):
    STOCKMANAGER_DIR_DEFAULT = os.path.expanduser("~/Documents/develop/StockManager")


@dataclass
class MCPConfig:
    """Connection parameters for StockManager MCP Server."""

    command: str = "uv"
    args: list[str] = field(
        default_factory=lambda: ["run", "python", "stockmanager-mcp/server.py"]
    )
    cwd: str = STOCKMANAGER_DIR_DEFAULT
    tushare_token: str | None = None
    # Timeout in seconds for tool calls (some tools like backtests are long-running)
    tool_timeout: float = 120.0


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class StockManagerMCPClient:
    """Async context manager wrapping a StockManager MCP stdio session.

    Example::

        async with StockManagerMCPClient() as client:
            names = await client.resolve_stock_name(["600519.SH"])
            print(names)

    When the MCP server is unavailable, methods return ``None`` or fall back
    gracefully so the caller can switch to local data sources.
    """

    def __init__(self, config: MCPConfig | None = None):
        self._config = config or MCPConfig()
        self._session: Any = None
        self._read: Any = None
        self._write: Any = None
        self._connected = False

    # -- context manager -------------------------------------------------------

    async def __aenter__(self) -> "StockManagerMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    async def connect(self) -> bool:
        """Establish the MCP stdio session. Returns True on success."""
        if self._connected:
            return True

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            token = self._config.tushare_token or os.environ.get("TUSHARE_TOKEN", "")
            server_params = StdioServerParameters(
                command=self._config.command,
                args=self._config.args,
                env={
                    "TUSHARE_TOKEN": token,
                    "PYTHONPATH": ".:stockmanager-mcp",
                    **{k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PATH")},
                },
                cwd=self._config.cwd,
            )

            self._read, self._write = await stdio_client(server_params).__aenter__()
            self._session = ClientSession(self._read, self._write)
            await self._session.__aenter__()
            await self._session.initialize()

            # Smoke test: list tools
            tools_result = await self._session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            logger.info(
                "Connected to StockManager MCP Server (%d tools available): %s",
                len(tool_names),
                ", ".join(tool_names),
            )
            self._connected = True
            return True

        except Exception as exc:
            logger.warning("Failed to connect to StockManager MCP: %s", exc)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Close the MCP session."""
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        self._read = None
        self._write = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # -- helper ----------------------------------------------------------------

    async def _call_tool(self, name: str, arguments: dict) -> Any:
        """Call an MCP tool and return parsed JSON, or None on failure."""
        if not self._connected:
            logger.warning("MCP not connected; skipping %s", name)
            return None
        try:
            import asyncio

            result = await asyncio.wait_for(
                self._session.call_tool(name, arguments),
                timeout=self._config.tool_timeout,
            )
            # result.content is a list of TextContent / ImageContent
            if result.content:
                text = getattr(result.content[0], "text", str(result.content[0]))
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return text
            return None
        except Exception as exc:
            logger.warning("MCP tool %s failed: %s", name, exc)
            return None

    # ===================================================================
    # Data Query Tools (8)
    # ===================================================================

    async def get_stock_daily(
        self, ts_codes: list[str], start_date: str, end_date: str, adj_type: str = "qfq"
    ) -> dict | None:
        """A股日线 OHLCV."""
        return await self._call_tool(
            "get_stock_daily",
            {"ts_codes": ts_codes, "start_date": start_date, "end_date": end_date, "adj_type": adj_type},
        )

    async def get_index_daily(
        self, index_code: str, start_date: str, end_date: str
    ) -> dict | None:
        """指数日线."""
        return await self._call_tool(
            "get_index_daily",
            {"index_code": index_code, "start_date": start_date, "end_date": end_date},
        )

    async def get_index_constituents(
        self, index_code: str, trade_date: str
    ) -> dict | None:
        """指数成分股."""
        return await self._call_tool(
            "get_index_constituents",
            {"index_code": index_code, "trade_date": trade_date},
        )

    async def get_trading_calendar(
        self, index_code: str, start_date: str, end_date: str
    ) -> dict | None:
        """A股交易日历."""
        return await self._call_tool(
            "get_trading_calendar",
            {"index_code": index_code, "start_date": start_date, "end_date": end_date},
        )

    async def get_institutional_flow(
        self, flow_type: str, start_date: str, end_date: str
    ) -> dict | None:
        """机构资金流 (northbound / margin / block_trade / lhb)."""
        return await self._call_tool(
            "get_institutional_flow",
            {"flow_type": flow_type, "start_date": start_date, "end_date": end_date},
        )

    async def get_financial_metrics(
        self, ts_code: str, end_date: str
    ) -> dict | None:
        """财务指标 (ROE / 利润增速)."""
        return await self._call_tool(
            "get_financial_metrics",
            {"ts_code": ts_code, "end_date": end_date},
        )

    async def get_industry_map(
        self, ts_codes: list[str] | None = None
    ) -> dict | None:
        """申万行业分类."""
        args: dict = {}
        if ts_codes:
            args["ts_codes"] = ts_codes
        return await self._call_tool("get_industry_map", args)

    async def get_risk_announcements(
        self, ts_code: str, start_date: str, end_date: str, keywords: list[str] | None = None
    ) -> dict | None:
        """风控公告扫描."""
        args = {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}
        if keywords:
            args["keywords"] = keywords
        return await self._call_tool("get_risk_announcements", args)

    # ===================================================================
    # Experiment Tools (8)
    # ===================================================================

    async def list_available_factors(self) -> dict | None:
        """列出可用因子."""
        return await self._call_tool("list_available_factors", {})

    async def list_strategies_and_configs(self) -> dict | None:
        """列出可用策略和配置."""
        return await self._call_tool("list_strategies_and_configs", {})

    async def evaluate_signal_formula(
        self, formula: str, start_date: str, end_date: str, universe_index: str = "000906.SH"
    ) -> dict | None:
        """信号公式 IC 评估."""
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
        """单因子评估."""
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
        """策略回测 (A股 T+1 约束)."""
        args: dict = {"strategy": strategy, "config": config, "start": start, "end": end}
        if initial_cash is not None:
            args["initial_cash"] = initial_cash
        if universe_size is not None:
            args["universe_size"] = universe_size
        return await self._call_tool("run_backtest", args)

    async def run_factor_experiment(self, experiment: dict) -> dict | None:
        """完整因子实验."""
        return await self._call_tool("run_factor_experiment", {"experiment": experiment})

    async def run_ablation_study(self, base_experiment: dict, ablations: list | None = None) -> dict | None:
        """因子消融实验."""
        args: dict = {"base_experiment": base_experiment}
        if ablations:
            args["ablations"] = ablations
        return await self._call_tool("run_ablation_study", args)

    async def get_job_status(self, job_id: str) -> dict | None:
        """异步任务状态查询."""
        return await self._call_tool("get_job_status", {"job_id": job_id})

    # ===================================================================
    # Advisor Tools (4)
    # ===================================================================

    async def resolve_stock_name(self, codes: list[str]) -> dict | None:
        """股票代码 → 中文名称."""
        return await self._call_tool("resolve_stock_name", {"codes": codes})

    async def generate_trading_plan(
        self, strategy: str, config: str, as_of_date: str,
        positions: list[dict], cash: float,
    ) -> dict | None:
        """生成明日交易计划 (Flight Plan)."""
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
        """执行滑点分析."""
        args: dict = {"trades": trades}
        if participation_rates:
            args["participation_rates"] = participation_rates
        return await self._call_tool("analyze_execution_slippage", args)

    async def compute_purged_cv_sharpe(
        self, equity_curve: list[dict], n_splits: int = 5, purge_days: int = 5,
    ) -> dict | None:
        """Purged CV Sharpe."""
        return await self._call_tool(
            "compute_purged_cv_sharpe",
            {"equity_curve": equity_curve, "n_splits": n_splits, "purge_days": purge_days},
        )


# ---------------------------------------------------------------------------
# Factory — for integration with FastAPI lifespan
# ---------------------------------------------------------------------------

_client_instance: StockManagerMCPClient | None = None


async def get_mcp_client(config: MCPConfig | None = None) -> StockManagerMCPClient | None:
    """Get or create the singleton MCP client.

    Returns None if the MCP server is unavailable (caller should degrade).
    """
    global _client_instance
    if _client_instance is not None and _client_instance.is_connected:
        return _client_instance

    client = StockManagerMCPClient(config)
    if await client.connect():
        _client_instance = client
        return client

    # Connection failed — existing caller retries next time
    return None


async def shutdown_mcp_client() -> None:
    """Disconnect the singleton MCP client (called during FastAPI shutdown)."""
    global _client_instance
    if _client_instance is not None:
        await _client_instance.disconnect()
        _client_instance = None
