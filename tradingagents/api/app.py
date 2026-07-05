"""FastAPI application factory."""

from contextlib import asynccontextmanager
import asyncio
from datetime import time
import logging
from pathlib import Path
import time as monotonic_time
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tradingagents.api.middleware.auth import AuthMiddleware
from tradingagents.core.mcp_client import get_mcp_client, get_mcp_status, shutdown_mcp_client
from tradingagents.core.orchestrator import Orchestrator
from tradingagents.core.persistence import Database
from tradingagents.core.reflection import ReflectionEngine
from tradingagents.core.run_manager import RunManager
from tradingagents.core.scheduler import Scheduler
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.skills.registry import SkillRegistry

from .routes import artifacts, config, health, plans, portfolio, profile, reflections, reports, runs, skills, trading_time
from .ws import stream


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — initialize and teardown."""
    registry = SkillRegistry()
    registry.auto_discover()

    import os

    db_path = os.environ.get("TRADINGAGENTS_APP_DB")
    db = Database(Path(db_path)) if db_path else Database()
    config = {**DEFAULT_CONFIG, **db.get_app_config()}

    # Import existing disk reports into SQLite for indexing
    results_dir = config.get("results_dir", "")
    if results_dir:
        imported = db.import_disk_reports(results_dir)
        if imported > 0:
            logging.getLogger(__name__).info("Imported %d existing reports from disk", imported)

    # Also check for reports in the CWD reports/ directory (CLI default save path)
    cwd_reports = Path.cwd() / "reports"
    if cwd_reports.exists():
        db.import_disk_reports(str(cwd_reports))

    # Resolve missing ticker names after startup. yfinance calls can block for a
    # long time, so this must not run before lifespan yields.
    app.state.ticker_backfill_task = None
    if config.get("ticker_name_backfill_enabled", True):
        app.state.ticker_backfill_task = asyncio.create_task(_backfill_ticker_names_async(db))

    app.state.registry = registry
    app.state.config = config
    app.state.db = db
    app.state.run_manager = RunManager(db=db)

    # Conditionally enable LLM-based intent routing
    llm_router = None
    if config.get("llm_routing_enabled", False):
        from tradingagents.core.llm_router import LLMRouter

        llm_router = LLMRouter(registry, config, db=db)

    app.state.orchestrator = Orchestrator(registry, config, db=db, llm_router=llm_router)
    app.state.scheduler = Scheduler()
    if config.get("scheduler_enabled", True):
        daily_skill = registry.get("daily_pipeline")
        if daily_skill is not None:
            async def run_daily_pipeline() -> None:
                # Skip on non-trading days (weekends/holidays) to avoid wasting
                # LLM tokens and producing signals dated to a non-trading day.
                from tradingagents.core.trading_time import get_temporal_context

                ctx = get_temporal_context(app.state.config, market="cn_a")
                if ctx.calendar_state != "trading_day":
                    logging.getLogger(__name__).info(
                        "Skipping daily_pipeline: non-trading day (%s)",
                        ctx.market_asof_date,
                    )
                    return
                await app.state.run_manager.create_run(
                    daily_skill,
                    {"limit": 5, "candidate_limit": 80},
                    app.state.config,
                )

            app.state.scheduler.register_daily("daily_pipeline", time(8, 30), run_daily_pipeline)

        # Reflection job — runs daily after market close
        async def run_reflection_pipeline() -> None:
            # Skip on non-trading days: there is no new close to reflect on, and
            # fetch_outcome would return None, leaving cases pending forever.
            from tradingagents.core.trading_time import get_temporal_context

            ctx = get_temporal_context(app.state.config, market="cn_a")
            if ctx.calendar_state != "trading_day":
                logging.getLogger(__name__).info(
                    "Skipping reflection_job: non-trading day (%s)",
                    ctx.market_asof_date,
                )
                return
            from tradingagents.agents.utils.memory import TradingMemoryLog

            memory_log = TradingMemoryLog(app.state.config)
            engine = ReflectionEngine(
                db=app.state.db,
                config=app.state.config,
                memory_log=memory_log,
            )
            result = await engine.run_reflection_batch()
            logging.getLogger(__name__).info("Reflection batch completed: %s", result)

        app.state.scheduler.register_daily("reflection_job", time(16, 30), run_reflection_pipeline)

        # Plan monitoring — runs after the reflection job, evaluates active
        # plans' conditions (price levels / golden cross / cash flow) against
        # the fresh close and flags triggers as plan_alert artifacts.
        async def run_plan_evaluation() -> None:
            from tradingagents.core.plan_monitor import evaluate_active_plans
            from tradingagents.core.trading_time import get_temporal_context

            ctx = get_temporal_context(app.state.config, market="cn_a")
            if ctx.calendar_state != "trading_day":
                return
            alerts = await evaluate_active_plans(app.state.db, app.state.config)
            if alerts:
                logging.getLogger(__name__).info(
                    "Plan evaluation triggered %d alert(s)", len(alerts)
                )

        app.state.scheduler.register_daily("plan_evaluation", time(16, 45), run_plan_evaluation)
        app.state.scheduler.start()
    # MCP init is wrapped in a timeout so a hung StockManager probe cannot block
    # FastAPI startup for the full tool_timeout (default 120s). On timeout we
    # continue in degraded mode (no MCP); the singleton will retry on next use.
    try:
        app.state.mcp_client = await asyncio.wait_for(
            get_mcp_client(config), timeout=10.0
        )
    except asyncio.TimeoutError:
        logging.getLogger(__name__).warning(
            "MCP client init timed out after 10s; continuing without MCP (degraded mode)"
        )
        app.state.mcp_client = None
    app.state.mcp_status = (
        app.state.mcp_client.status
        if app.state.mcp_client is not None
        else await get_mcp_status(config)
    )
    app.state.mcp_status_checked_at = monotonic_time.monotonic()

    yield

    await app.state.run_manager.cancel_all()
    backfill_task = getattr(app.state, "ticker_backfill_task", None)
    if backfill_task and not backfill_task.done():
        backfill_task.cancel()
    await app.state.scheduler.stop()
    await shutdown_mcp_client()


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="TradingAgents API",
        version="1.0.0",
        description="Multi-agent financial trading analysis platform",
        lifespan=lifespan,
    )

    # CORS for local development and Tauri
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "tauri://localhost"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Optional token auth (no-op when api_auth_token is empty, the default).
    app.add_middleware(AuthMiddleware)

    # Register REST routes
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(skills.router, prefix="/api/v1", tags=["skills"])
    app.include_router(runs.router, prefix="/api/v1", tags=["runs"])
    app.include_router(artifacts.router, prefix="/api/v1", tags=["artifacts"])
    app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
    app.include_router(config.router, prefix="/api/v1", tags=["config"])
    app.include_router(profile.router, prefix="/api/v1", tags=["profile"])
    app.include_router(portfolio.router, prefix="/api/v1", tags=["portfolio"])
    app.include_router(plans.router, prefix="/api/v1", tags=["plans"])
    app.include_router(reflections.router, prefix="/api/v1", tags=["reflections"])
    app.include_router(trading_time.router, prefix="/api/v1", tags=["trading-time"])

    # Register WebSocket routes
    app.include_router(stream.router)

    return app


async def _backfill_ticker_names_async(db: Database) -> None:
    try:
        backfilled = await asyncio.to_thread(db.backfill_ticker_names)
        if backfilled > 0:
            logging.getLogger(__name__).info("Resolved %d ticker names", backfilled)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.getLogger(__name__).warning("Ticker name backfill failed: %s", exc)
