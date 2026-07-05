"""SQLite persistence layer for run history and reports.

Stores run metadata and report content so the frontend can display
historical data without re-running analyses.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path.home() / ".tradingagents" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    ticker TEXT NOT NULL,
    ticker_name TEXT,
    rating TEXT,
    content TEXT NOT NULL,
    report_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdings (
    symbol TEXT PRIMARY KEY,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    current_price REAL,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    investment_style TEXT NOT NULL DEFAULT 'long_term',
    risk_tolerance TEXT NOT NULL DEFAULT 'moderate',
    sector_prefs TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    signal TEXT NOT NULL,
    final_score REAL,
    quant_score REAL,
    llm_confidence REAL,
    fusion_mode TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    skill_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    subject_type TEXT,
    subject_id TEXT,
    subject_name TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    summary TEXT,
    content_markdown TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_skill ON runs(skill_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_reports_ticker ON reports(ticker);
CREATE INDEX IF NOT EXISTS idx_reports_run ON reports(run_id);
CREATE INDEX IF NOT EXISTS idx_signals_trade_date ON signals(trade_date);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_skill ON artifacts(skill_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);
CREATE INDEX IF NOT EXISTS idx_artifacts_subject ON artifacts(subject_type, subject_id);

CREATE TABLE IF NOT EXISTS reflections (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    ticker TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    original_decision TEXT NOT NULL,
    actual_return REAL,
    was_correct BOOLEAN,
    reflection_text TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reflections_ticker ON reflections(ticker);
CREATE INDEX IF NOT EXISTS idx_reflections_trade_date ON reflections(trade_date);

CREATE TABLE IF NOT EXISTS reflection_cases (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    reflection_scope TEXT NOT NULL,
    eligible_for_strategy_learning INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    symbol TEXT NOT NULL,
    name TEXT,
    signal_date TEXT NOT NULL,
    horizon_days INTEGER NOT NULL DEFAULT 5,
    source_run_id TEXT NOT NULL DEFAULT '',
    source_artifact_id TEXT NOT NULL DEFAULT '',
    snapshot_payload TEXT NOT NULL DEFAULT '{}',
    outcome_payload TEXT NOT NULL DEFAULT '{}',
    post_signal_evidence_payload TEXT NOT NULL DEFAULT '{}',
    attribution_payload TEXT NOT NULL DEFAULT '{}',
    lesson_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reflection_cases_symbol ON reflection_cases(symbol);
CREATE INDEX IF NOT EXISTS idx_reflection_cases_status ON reflection_cases(status);
CREATE INDEX IF NOT EXISTS idx_reflection_cases_scope ON reflection_cases(reflection_scope);
CREATE INDEX IF NOT EXISTS idx_reflection_cases_signal_date ON reflection_cases(signal_date);

CREATE TABLE IF NOT EXISTS strategy_lessons (
    id TEXT PRIMARY KEY,
    lesson_type TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    target TEXT NOT NULL DEFAULT '',
    finding TEXT NOT NULL,
    suggested_adjustment TEXT NOT NULL DEFAULT '',
    evidence_count INTEGER NOT NULL DEFAULT 1,
    confidence TEXT NOT NULL DEFAULT 'low',
    active INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_lessons_active ON strategy_lessons(active);
CREATE INDEX IF NOT EXISTS idx_strategy_lessons_type ON strategy_lessons(lesson_type);
CREATE INDEX IF NOT EXISTS idx_strategy_lessons_scope ON strategy_lessons(scope, target);

CREATE TABLE IF NOT EXISTS artifact_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT,
    subtitle TEXT,
    status TEXT,
    summary TEXT,
    content_markdown TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    saved_at TEXT NOT NULL,
    UNIQUE(artifact_id, version)
);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_artifact ON artifact_versions(artifact_id, version);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, seq);
"""


class Database:
    """SQLite persistence layer."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # Migration: add ticker_name column if missing
            try:
                conn.execute("SELECT ticker_name FROM reports LIMIT 0")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE reports ADD COLUMN ticker_name TEXT")
            self._backfill_report_artifacts(conn)

    @contextmanager
    def _conn(self):
        # WAL + busy_timeout so concurrent readers don't block the writer and
        # short-lived lock contention waits instead of raising "database is
        # locked" immediately. PRAGMAs are cheap and idempotent.
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _backfill_report_artifacts(self, conn: sqlite3.Connection) -> None:
        """Create stock_report artifacts for reports saved before Library existed."""
        rows = conn.execute(
            """
            SELECT r.* FROM reports r
            LEFT JOIN artifacts a ON a.id = r.id
            WHERE a.id IS NULL
            """
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            item = dict(row)
            ticker = item.get("ticker") or ""
            ticker_name = item.get("ticker_name") or ticker
            created_at = item.get("created_at") or now
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts (
                    id, run_id, skill_id, artifact_type, title, subtitle,
                    subject_type, subject_id, subject_name, status, summary,
                    content_markdown, payload_json, tags_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item.get("run_id") or "",
                    "stock_analysis",
                    "stock_report",
                    f"{ticker_name} 投资报告",
                    item.get("rating"),
                    "ticker",
                    ticker,
                    ticker_name,
                    "success",
                    item.get("rating"),
                    item.get("content") or "",
                    json.dumps({"report_path": item.get("report_path"), "rating": item.get("rating")}, ensure_ascii=False),
                    json.dumps(["stock_report", ticker], ensure_ascii=False),
                    created_at,
                    now,
                ),
            )

    def save_run(self, run_id: str, skill_id: str, params: dict, status: str) -> None:
        """Insert a new run record."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (id, skill_id, params, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, skill_id, json.dumps(params), status, datetime.now(timezone.utc).isoformat()),
            )

    def update_run_status(self, run_id: str, status: str, **kwargs) -> None:
        """Update a run's status and optional fields."""
        sets = ["status = ?"]
        values = [status]
        for key in ("result", "error", "started_at", "completed_at"):
            if key in kwargs:
                sets.append(f"{key} = ?")
                val = kwargs[key]
                values.append(json.dumps(val) if isinstance(val, dict) else val)
        values.append(run_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", values)

    def save_run_event(self, run_id: str, seq: int, event_type: str, payload: dict) -> None:
        """Persist a run event so a reconnecting client can replay progress
        even after the server restarts (the in-memory ``Run.events`` list is
        lost on restart). Best-effort: a write failure is logged, not raised."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO run_events (run_id, seq, event_type, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        run_id,
                        int(seq),
                        event_type,
                        json.dumps(payload or {}, ensure_ascii=False, default=str),
                        now,
                    ),
                )
        except Exception:
            # Event persistence must never break the run pipeline.
            pass

    def list_run_events(self, run_id: str) -> list[dict]:
        """Replay persisted events for a run, ordered by sequence."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event_type, payload_json, created_at, seq FROM run_events "
                "WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["payload"] = json.loads(d.pop("payload_json", "{}") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["payload"] = {}
            result.append(d)
        return result

    def save_report(
        self,
        report_id: str,
        run_id: str,
        ticker: str,
        rating: str,
        content: str,
        path: str | None,
        ticker_name: str | None = None,
    ) -> None:
        """Save a completed report."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO reports (id, run_id, ticker, ticker_name, rating, content, report_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, run_id, ticker, ticker_name, rating, content, path, created_at),
            )
        self.save_artifact(
            artifact_id=report_id,
            run_id=run_id,
            skill_id="stock_analysis",
            artifact_type="stock_report",
            title=f"{ticker_name or ticker} 投资报告",
            subtitle=rating,
            subject_type="ticker",
            subject_id=ticker,
            subject_name=ticker_name or ticker,
            status="success",
            summary=rating,
            content_markdown=content,
            payload={"report_path": path, "rating": rating},
            tags=["stock_report", ticker],
            created_at=created_at,
        )

    def list_reports(self, limit: int = 50, ticker: str | None = None) -> list[dict]:
        """List reports, optionally filtered by ticker."""
        query = "SELECT * FROM reports"
        params: list[Any] = []
        if ticker:
            query += " WHERE ticker = ?"
            params.append(ticker)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List recent runs with pagination."""
        limit = max(0, min(limit, 200))
        offset = max(0, offset)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict | None:
        """Get a run by ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def get_report(self, report_id: str) -> dict | None:
        """Get a report by ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
            return dict(row) if row else None

    def save_artifact(
        self,
        artifact_id: str,
        run_id: str,
        skill_id: str,
        artifact_type: str,
        title: str,
        subtitle: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        subject_name: str | None = None,
        status: str = "success",
        summary: str | None = None,
        content_markdown: str | None = None,
        payload: dict | None = None,
        tags: list[str] | None = None,
        created_at: str | None = None,
    ) -> None:
        """Save a cross-skill artifact for the Library view.

        If an artifact with the same id already exists, its mutable fields are
        snapshotted into ``artifact_versions`` before the overwrite so the
        Library can show how a report evolved across re-runs.
        """
        now = datetime.now(timezone.utc).isoformat()
        created = created_at or now
        with self._conn() as conn:
            # Snapshot the existing version before replacing (if any).
            existing = conn.execute(
                "SELECT title, subtitle, status, summary, content_markdown, "
                "payload_json, updated_at FROM artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
            if existing is not None:
                next_version = (
                    conn.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM artifact_versions WHERE artifact_id = ?",
                        (artifact_id,),
                    ).fetchone()[0]
                    + 1
                )
                conn.execute(
                    "INSERT INTO artifact_versions (artifact_id, version, title, subtitle, "
                    "status, summary, content_markdown, payload_json, saved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        artifact_id,
                        next_version,
                        existing["title"],
                        existing["subtitle"],
                        existing["status"],
                        existing["summary"],
                        existing["content_markdown"],
                        existing["payload_json"],
                        existing["updated_at"] or now,
                    ),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts (
                    id, run_id, skill_id, artifact_type, title, subtitle,
                    subject_type, subject_id, subject_name, status, summary,
                    content_markdown, payload_json, tags_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    run_id or "",
                    skill_id,
                    artifact_type,
                    title,
                    subtitle,
                    subject_type,
                    subject_id,
                    subject_name,
                    status,
                    summary,
                    content_markdown,
                    json.dumps(payload or {}, ensure_ascii=False),
                    json.dumps(tags or [], ensure_ascii=False),
                    created,
                    now,
                ),
            )

    def list_artifacts(
        self,
        limit: int = 50,
        skill_id: str | None = None,
        artifact_type: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        run_id: str | None = None,
        q: str | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """List Library artifacts with optional filters and pagination."""
        limit = max(0, min(limit, 200))
        offset = max(0, offset)
        query = "SELECT * FROM artifacts"
        clauses: list[str] = []
        params: list[Any] = []
        if skill_id:
            clauses.append("skill_id = ?")
            params.append(skill_id)
        if artifact_type:
            clauses.append("artifact_type = ?")
            params.append(artifact_type)
        if subject_type:
            clauses.append("subject_type = ?")
            params.append(subject_type)
        if subject_id:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if q:
            like = f"%{q}%"
            clauses.append("(title LIKE ? OR summary LIKE ? OR subject_id LIKE ? OR subject_name LIKE ?)")
            params.extend([like, like, like, like])
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_artifact(dict(row)) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict | None:
        """Get a single Library artifact."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._decode_artifact(dict(row)) if row else None

    def list_artifact_versions(self, artifact_id: str) -> list[dict]:
        """List historical snapshots of an artifact, newest version first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id = ? "
                "ORDER BY version DESC",
                (artifact_id,),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["payload"] = json.loads(d.pop("payload_json", "{}") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["payload"] = {}
            result.append(d)
        return result

    def list_artifacts_for_run(self, run_id: str) -> list[dict]:
        """List all artifacts associated with a run."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at DESC",
                (run_id,),
            ).fetchall()
        return [self._decode_artifact(dict(row)) for row in rows]

    def _decode_artifact(self, row: dict) -> dict:
        try:
            row["payload"] = json.loads(row.pop("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            row["payload"] = {}
        try:
            row["tags"] = json.loads(row.pop("tags_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            row["tags"] = []
        return row

    def save_signal(
        self,
        signal_id: str,
        run_id: str,
        trade_date: str,
        symbol: str,
        name: str | None,
        signal: str,
        final_score: float | None,
        quant_score: float | None,
        llm_confidence: float | None,
        fusion_mode: str | None,
        payload: dict,
    ) -> None:
        """Save a structured trading signal for later review/backtest."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signals (
                    id, run_id, trade_date, symbol, name, signal,
                    final_score, quant_score, llm_confidence, fusion_mode,
                    payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    run_id,
                    trade_date,
                    symbol.strip().upper(),
                    name,
                    signal,
                    final_score,
                    quant_score,
                    llm_confidence,
                    fusion_mode,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def list_signals(self, limit: int = 50, trade_date: str | None = None) -> list[dict]:
        """List structured trading signals."""
        query = "SELECT * FROM signals"
        params: list[Any] = []
        if trade_date:
            query += " WHERE trade_date = ?"
            params.append(trade_date)
        query += " ORDER BY trade_date DESC, final_score DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            result.append(item)
        return result

    # ------------------------------------------------------------------
    # Reflection cases and strategy lessons
    # ------------------------------------------------------------------

    def save_reflection_case(
        self,
        case_id: str,
        source_type: str,
        reflection_scope: str,
        eligible_for_strategy_learning: bool,
        symbol: str,
        signal_date: str,
        horizon_days: int = 5,
        source_run_id: str = "",
        source_artifact_id: str = "",
        name: str | None = None,
        snapshot_payload: dict | None = None,
        outcome_payload: dict | None = None,
        post_signal_evidence_payload: dict | None = None,
        attribution_payload: dict | None = None,
        lesson_payload: dict | None = None,
        status: str = "pending",
    ) -> None:
        """Save or replace a reflection case with full signal-time context."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reflection_cases (
                    id, source_type, reflection_scope, eligible_for_strategy_learning,
                    status, symbol, name, signal_date, horizon_days, source_run_id,
                    source_artifact_id, snapshot_payload, outcome_payload,
                    post_signal_evidence_payload, attribution_payload, lesson_payload,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    source_type,
                    reflection_scope,
                    1 if eligible_for_strategy_learning else 0,
                    status,
                    symbol.strip().upper(),
                    name,
                    signal_date,
                    horizon_days,
                    source_run_id or "",
                    source_artifact_id or "",
                    json.dumps(snapshot_payload or {}, ensure_ascii=False),
                    json.dumps(outcome_payload or {}, ensure_ascii=False),
                    json.dumps(post_signal_evidence_payload or {}, ensure_ascii=False),
                    json.dumps(attribution_payload or {}, ensure_ascii=False),
                    json.dumps(lesson_payload or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def update_reflection_case(
        self,
        case_id: str,
        *,
        status: str | None = None,
        outcome_payload: dict | None = None,
        post_signal_evidence_payload: dict | None = None,
        attribution_payload: dict | None = None,
        lesson_payload: dict | None = None,
        eligible_for_strategy_learning: bool | None = None,
    ) -> None:
        """Update mutable reflection case fields after outcome/attribution."""
        sets: list[str] = ["updated_at = ?"]
        values: list[Any] = [datetime.now(timezone.utc).isoformat()]
        if status is not None:
            sets.append("status = ?")
            values.append(status)
        if outcome_payload is not None:
            sets.append("outcome_payload = ?")
            values.append(json.dumps(outcome_payload, ensure_ascii=False))
        if post_signal_evidence_payload is not None:
            sets.append("post_signal_evidence_payload = ?")
            values.append(json.dumps(post_signal_evidence_payload, ensure_ascii=False))
        if attribution_payload is not None:
            sets.append("attribution_payload = ?")
            values.append(json.dumps(attribution_payload, ensure_ascii=False))
        if lesson_payload is not None:
            sets.append("lesson_payload = ?")
            values.append(json.dumps(lesson_payload, ensure_ascii=False))
        if eligible_for_strategy_learning is not None:
            sets.append("eligible_for_strategy_learning = ?")
            values.append(1 if eligible_for_strategy_learning else 0)
        values.append(case_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE reflection_cases SET {', '.join(sets)} WHERE id = ?", values)

    def list_reflection_cases(
        self,
        limit: int = 50,
        status: str | None = None,
        symbol: str | None = None,
        reflection_scope: str | None = None,
        eligible_only: bool = False,
    ) -> list[dict]:
        """List reflection cases with decoded JSON payloads."""
        query = "SELECT * FROM reflection_cases"
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.strip().upper())
        if reflection_scope:
            clauses.append("reflection_scope = ?")
            params.append(reflection_scope)
        if eligible_only:
            clauses.append("eligible_for_strategy_learning = 1")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY signal_date DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_reflection_case(dict(row)) for row in rows]

    def prune_reflection_cases(
        self,
        older_than_days: int = 90,
        status: str = "reflected",
    ) -> int:
        """Delete reflected (or other terminal-status) cases older than the cutoff.

        Prevents unbounded growth of the reflection_cases table — daily_pipeline
        creates up to ``candidate_limit`` pending cases per run while the
        reflection job only resolves ``max_per_run`` per day, so without pruning
        the table grows without bound. Returns the number of rows deleted.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM reflection_cases WHERE status = ? AND updated_at < ?",
                (status, cutoff),
            )
            return int(cur.rowcount or 0)

    def get_reflection_case(self, case_id: str) -> dict | None:
        with self._conn() as conn:            row = conn.execute("SELECT * FROM reflection_cases WHERE id = ?", (case_id,)).fetchone()
        return self._decode_reflection_case(dict(row)) if row else None

    def _decode_reflection_case(self, row: dict) -> dict:
        row["eligible_for_strategy_learning"] = bool(row.get("eligible_for_strategy_learning"))
        for key in (
            "snapshot_payload",
            "outcome_payload",
            "post_signal_evidence_payload",
            "attribution_payload",
            "lesson_payload",
        ):
            try:
                row[key] = json.loads(row.get(key) or "{}")
            except (json.JSONDecodeError, TypeError):
                row[key] = {}
        return row

    def save_strategy_lesson(
        self,
        lesson_id: str,
        lesson_type: str,
        scope: str,
        finding: str,
        suggested_adjustment: str = "",
        target: str = "",
        evidence_count: int = 1,
        confidence: str = "low",
        active: bool = True,
        expires_at: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Persist a reusable strategy lesson."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_lessons (
                    id, lesson_type, scope, target, finding, suggested_adjustment,
                    evidence_count, confidence, active, expires_at, payload_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lesson_id,
                    lesson_type,
                    scope,
                    target or "",
                    finding,
                    suggested_adjustment,
                    int(evidence_count),
                    confidence,
                    1 if active else 0,
                    expires_at,
                    json.dumps(payload or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def list_strategy_lessons(
        self,
        limit: int = 20,
        active_only: bool = True,
        lesson_type: str | None = None,
    ) -> list[dict]:
        """List reusable strategy lessons."""
        query = "SELECT * FROM strategy_lessons"
        clauses: list[str] = []
        params: list[Any] = []
        if active_only:
            clauses.append("active = 1")
        if lesson_type:
            clauses.append("lesson_type = ?")
            params.append(lesson_type)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["active"] = bool(item.get("active"))
            try:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                item["payload"] = {}
            result.append(item)
        return result

    def upsert_holding(
        self,
        symbol: str,
        quantity: float,
        avg_cost: float,
        current_price: float | None = None,
        notes: str | None = None,
    ) -> dict:
        """Create or update a holding."""
        now = datetime.now(timezone.utc).isoformat()
        normalized = symbol.strip().upper()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO holdings (symbol, quantity, avg_cost, current_price, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    avg_cost = excluded.avg_cost,
                    current_price = excluded.current_price,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (normalized, quantity, avg_cost, current_price, notes, now),
            )
        return self.get_holding(normalized) or {
            "symbol": normalized,
            "quantity": quantity,
            "avg_cost": avg_cost,
            "current_price": current_price,
            "notes": notes,
            "updated_at": now,
        }

    def delete_holding(self, symbol: str) -> bool:
        """Delete a holding by symbol."""
        normalized = symbol.strip().upper()
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM holdings WHERE symbol = ?", (normalized,))
            return cursor.rowcount > 0

    def get_holding(self, symbol: str) -> dict | None:
        """Get one holding by symbol."""
        normalized = symbol.strip().upper()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM holdings WHERE symbol = ?", (normalized,)
            ).fetchone()
            return dict(row) if row else None

    def update_holding_price(self, symbol: str, current_price: float) -> bool:
        """Update only ``current_price`` for a holding.

        Unlike :meth:`upsert_holding` (which rewrites quantity/avg_cost/notes
        and is therefore a read-modify-write race when the price refresher runs
        concurrently with a user edit), this touches only the price column and
        ``updated_at``. Returns True if a row was updated.
        """
        normalized = symbol.strip().upper()
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE holdings SET current_price = ?, updated_at = ? WHERE symbol = ?",
                (current_price, now, normalized),
            )
            return cursor.rowcount > 0

    def search_ticker_by_name(self, text: str) -> str | None:
        """Resolve a company name embedded in ``text`` to a ticker.

        Best-effort lookup that loads distinct ``ticker_name`` values from
        reports and checks whether any appears as a substring of ``text``. This
        covers names not in the hardcoded ``NAME_TO_TICKER`` alias table (e.g.
        "生益科技" resolves if the user has analyzed it before). Returns the
        first matching ticker or None.
        """
        cleaned = (text or "").strip()
        if not cleaned or len(cleaned) < 2:
            return None
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ticker_name, ticker FROM reports "
                "WHERE ticker_name IS NOT NULL AND length(ticker_name) >= 2 "
                "ORDER BY created_at DESC LIMIT 500"
            ).fetchall()
        # Longer names first so "宁德时代" wins over a generic shorter substring.
        candidates = sorted(
            (dict(r) for r in rows if r["ticker_name"] and r["ticker"]),
            key=lambda r: len(r["ticker_name"]),
            reverse=True,
        )
        for row in candidates:
            name = row["ticker_name"]
            if name and name in cleaned:
                return row["ticker"]
        return None

    def list_holdings(self) -> list[dict]:
        """List all holdings."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM holdings ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_user_profile(self) -> dict:
        """Return the persisted user profile or create the default row."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
            if row is None:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO user_profile
                        (id, investment_style, risk_tolerance, sector_prefs, updated_at)
                    VALUES (1, 'long_term', 'moderate', '[]', ?)
                    """,
                    (now,),
                )
                row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
            data = dict(row)
        data["sector_prefs"] = json.loads(data.get("sector_prefs") or "[]")
        return data

    def update_user_profile(
        self,
        investment_style: str | None = None,
        risk_tolerance: str | None = None,
        sector_prefs: list[str] | None = None,
    ) -> dict:
        """Update the singleton user profile."""
        current = self.get_user_profile()
        values = {
            "investment_style": investment_style or current["investment_style"],
            "risk_tolerance": risk_tolerance or current["risk_tolerance"],
            "sector_prefs": sector_prefs if sector_prefs is not None else current["sector_prefs"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_profile
                    (id, investment_style, risk_tolerance, sector_prefs, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    investment_style = excluded.investment_style,
                    risk_tolerance = excluded.risk_tolerance,
                    sector_prefs = excluded.sector_prefs,
                    updated_at = excluded.updated_at
                """,
                (
                    values["investment_style"],
                    values["risk_tolerance"],
                    json.dumps(values["sector_prefs"], ensure_ascii=False),
                    values["updated_at"],
                ),
            )
        return self.get_user_profile()

    def get_app_config(self) -> dict[str, Any]:
        """Return persisted runtime config overrides."""
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM app_config").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                result[row["key"]] = row["value"]
        return result

    def update_app_config(self, values: dict[str, Any]) -> dict[str, Any]:
        """Persist runtime config overrides and return the merged override set."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO app_config (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, json.dumps(value, ensure_ascii=False), now),
                )
        return self.get_app_config()

    def report_path_exists(self, path: str) -> bool:
        """Check if a report with the given path already exists."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM reports WHERE report_path = ?", (path,)
            ).fetchone()
            return row is not None

    @staticmethod
    def resolve_ticker_name(ticker: str) -> str | None:
        """Resolve a ticker to its company name. Best-effort, returns None on failure."""
        # Normalize A-share tickers for yfinance
        yf_ticker = ticker
        pure_digits = ticker.replace(".SS", "").replace(".SZ", "").replace(".SH", "")
        if pure_digits.isdigit() and len(pure_digits) == 6:
            if pure_digits.startswith("6"):
                yf_ticker = f"{pure_digits}.SS"
            elif pure_digits.startswith(("0", "3")):
                yf_ticker = f"{pure_digits}.SZ"
            elif pure_digits.startswith("8") or pure_digits.startswith("4"):
                yf_ticker = f"{pure_digits}.BJ"
        try:
            import yfinance as yf
            t = yf.Ticker(yf_ticker)
            info = t.info or {}
            name = info.get("shortName") or info.get("longName")
            if name:
                return name
        except Exception:
            pass
        return None

    @staticmethod
    def extract_name_from_content(content: str, ticker: str) -> str | None:
        """Extract company name from report content as fallback."""
        import re

        GENERIC_NAMES = {
            "沪市股票", "深市股票", "A股主板", "A股", "创业板", "科创板",
            "北交所", "Shanghai stock", "Shenzhen stock", "A-share",
        }

        patterns = [
            rf"{re.escape(ticker)}[（(]([^）)]+)[）)]",
            rf"{re.escape(ticker.replace('.SS', '').replace('.SZ', '').replace('.SH', ''))}[（(]([^）)]+)[）)]",
            r"\*\*(?:标的|公司|股票|股票名称|公司名称|标的名称)[^*]*\*\*[:：]\s*([^\n,，*|]+)",
            r"(?:Company|公司|股票名称|标的)[:：]\s*([^\n,，*|]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, content[:3000])
            if match:
                name = match.group(1).strip()
                if 2 <= len(name) <= 20 and name not in GENERIC_NAMES:
                    return name
        return None

    def backfill_ticker_names(self) -> int:
        """Resolve missing ticker names for existing reports."""
        updated = 0
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, ticker, content FROM reports WHERE ticker_name IS NULL OR ticker_name = ''"
            ).fetchall()
        for row in rows:
            row_dict = dict(row)
            ticker = row_dict["ticker"]
            name = self.resolve_ticker_name(ticker)
            if not name:
                name = self.extract_name_from_content(
                    row_dict.get("content", ""), ticker
                )
            if name:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE reports SET ticker_name = ? WHERE id = ?",
                        (name, row_dict["id"]),
                    )
                updated += 1
        return updated

    def import_disk_reports(self, results_dir: str | Path) -> int:
        """Scan results directory for saved reports and import into DB.

        Handles two directory layouts:
        1. API skill format: ``<dir>/<ticker>_<ts>/reports/complete_report.md``
        2. CLI format: ``<dir>/<ticker>/<date>/reports/final_trade_decision.md``
           with individual section .md files alongside it.

        Returns the number of newly imported reports.
        """
        import re
        import uuid

        results_path = Path(results_dir)
        if not results_path.exists():
            return 0

        imported = 0

        # Format 1: complete_report.md files
        for report_file in results_path.rglob("complete_report.md"):
            report_dir = report_file.parent
            if self.report_path_exists(str(report_dir)):
                continue
            try:
                content = report_file.read_text(encoding="utf-8")
                first_line = content.split("\n", 1)[0]
                ticker = "UNKNOWN"
                rating = "Unknown"
                if ":" in first_line:
                    ticker = first_line.split(":", 1)[1].strip().split("(")[0].strip().split(" ")[0]
                match = re.search(r"\*\*Rating\*\*:\s*(\w+)", content)
                if match:
                    rating = match.group(1)
                mtime = report_dir.stat().st_mtime
                created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                self.save_report(
                    report_id=str(uuid.uuid4()), run_id="", ticker=ticker,
                    rating=rating, content=content, path=str(report_dir),
                )
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE reports SET created_at = ? WHERE id = (SELECT id FROM reports WHERE report_path = ? ORDER BY created_at DESC LIMIT 1)",
                        (created_at, str(report_dir)),
                    )
                imported += 1
            except Exception:
                continue

        # Format 2: CLI per-section files (final_trade_decision.md present)
        section_files = [
            ("market_report", "market_report.md"),
            ("sentiment_report", "sentiment_report.md"),
            ("news_report", "news_report.md"),
            ("fundamentals_report", "fundamentals_report.md"),
            ("investment_plan", "investment_plan.md"),
            ("trader_investment_plan", "trader_investment_plan.md"),
            ("final_trade_decision", "final_trade_decision.md"),
        ]
        for decision_file in results_path.rglob("final_trade_decision.md"):
            report_dir = decision_file.parent
            if self.report_path_exists(str(report_dir)):
                continue
            try:
                parts = []
                for section_key, filename in section_files:
                    fp = report_dir / filename
                    if fp.exists():
                        text = fp.read_text(encoding="utf-8")
                        title = section_key.replace("_", " ").title()
                        parts.append(f"## {title}\n\n{text}")

                if not parts:
                    continue

                content = "\n\n".join(parts)
                # Extract ticker from parent directory name
                # CLI layout: <results_dir>/<ticker>/<date>/reports/
                date_dir = report_dir.parent  # the date directory
                ticker_dir = date_dir.parent  # the ticker directory
                ticker = ticker_dir.name

                # Extract rating from final_trade_decision
                rating = "Unknown"
                match = re.search(r"\*\*Rating\*\*:\s*(\w+)", content)
                if match:
                    rating = match.group(1)
                if rating == "Unknown":
                    match = re.search(r"(?i)\b(buy|overweight|hold|underweight|sell)\b", decision_file.read_text())
                    if match:
                        rating = match.group(1).capitalize()

                mtime = report_dir.stat().st_mtime
                created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

                self.save_report(
                    report_id=str(uuid.uuid4()), run_id="", ticker=ticker,
                    rating=rating, content=content, path=str(report_dir),
                )
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE reports SET created_at = ? WHERE id = (SELECT id FROM reports WHERE report_path = ? ORDER BY created_at DESC LIMIT 1)",
                        (created_at, str(report_dir)),
                    )
                imported += 1
            except Exception:
                continue

        return imported

    # ------------------------------------------------------------------
    # Reflections
    # ------------------------------------------------------------------

    def save_reflection(self, entry: dict) -> None:
        """Save a reflection record."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO reflections "
                "(id, run_id, ticker, trade_date, original_decision, actual_return, was_correct, reflection_text, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["id"],
                    entry.get("run_id") or "",
                    entry["ticker"],
                    entry["trade_date"],
                    entry["original_decision"],
                    entry.get("actual_return"),
                    entry.get("was_correct"),
                    entry.get("reflection_text") or "",
                    entry.get("created_at") or datetime.now(timezone.utc).isoformat(),
                ),
            )

    def list_reflections(self, ticker: str | None = None, limit: int = 50) -> list[dict]:
        """List reflections, optionally filtered by ticker."""
        query = "SELECT * FROM reflections"
        params: list[Any] = []
        if ticker:
            query += " WHERE ticker = ?"
            params.append(ticker)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_reflection_summary(self, lookback_days: int = 30) -> dict:
        """Get aggregated reflection statistics.

        Neutral decisions (``was_correct IS NULL``, e.g. WATCHLIST/HOLD) are
        excluded from the accuracy denominator and reported separately so they
        no longer inflate the accuracy figure.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM reflections WHERE created_at >= ?", (cutoff,)
            ).fetchone()[0]
            neutral = conn.execute(
                "SELECT COUNT(*) FROM reflections WHERE created_at >= ? AND was_correct IS NULL",
                (cutoff,),
            ).fetchone()[0]
            correct = conn.execute(
                "SELECT COUNT(*) FROM reflections WHERE created_at >= ? AND was_correct = 1", (cutoff,)
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT * FROM reflections WHERE created_at >= ? ORDER BY created_at DESC LIMIT 5", (cutoff,)
            ).fetchall()
        # Denominator excludes neutral decisions (BUY/SELL only).
        scored = total - neutral
        incorrect = scored - correct
        accuracy = (correct / scored) if scored > 0 else 0.0
        return {
            "total": total,
            "neutral": neutral,
            "correct": correct,
            "incorrect": incorrect,
            "accuracy": round(accuracy, 4),
            "lookback_days": lookback_days,
            "recent": [dict(row) for row in recent],
        }
