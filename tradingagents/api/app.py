"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tradingagents.core.orchestrator import Orchestrator
from tradingagents.core.persistence import Database
from tradingagents.core.run_manager import RunManager
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.skills.registry import SkillRegistry

from .routes import config, health, reports, runs, skills
from .ws import stream


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — initialize and teardown."""
    import logging

    registry = SkillRegistry()
    registry.auto_discover()

    config = dict(DEFAULT_CONFIG)
    import os

    db_path = os.environ.get("TRADINGAGENTS_APP_DB")
    db = Database(Path(db_path)) if db_path else Database()

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

    # Resolve missing ticker names (best-effort, non-blocking)
    backfilled = db.backfill_ticker_names()
    if backfilled > 0:
        logging.getLogger(__name__).info("Resolved %d ticker names", backfilled)

    app.state.registry = registry
    app.state.config = config
    app.state.db = db
    app.state.run_manager = RunManager(db=db)
    app.state.orchestrator = Orchestrator(registry, config)

    yield

    await app.state.run_manager.cancel_all()


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

    # Register REST routes
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(skills.router, prefix="/api/v1", tags=["skills"])
    app.include_router(runs.router, prefix="/api/v1", tags=["runs"])
    app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
    app.include_router(config.router, prefix="/api/v1", tags=["config"])

    # Register WebSocket routes
    app.include_router(stream.router)

    return app
