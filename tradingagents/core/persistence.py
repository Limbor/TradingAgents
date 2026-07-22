"""SQLite persistence layer for run history and reports.

Stores run metadata and report content so the frontend can display
historical data without re-running analyses.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradingagents.dataflows.symbol_utils import (
    detect_market,
    is_yahoo_safe,
    normalize_cn_display,
)

logger = logging.getLogger(__name__)


DB_PATH = Path.home() / ".tradingagents" / "app.db"

_NON_SECURITY_SYMBOLS = {
    "DAILY_PIPELINE",
    "DAILY_REVIEW",
    "MARKET_SCANNER",
    "POSITION_ADVISOR",
    "PORTFOLIO",
    "CN_A",
    "UNKNOWN",
}


def _normalize_audit_symbol(symbol: Any) -> str | None:
    """Return a canonical market symbol or ``None`` for workflow labels.

    The audit ledger accepts both A-share and international Yahoo-style
    symbols, but it must never treat artifact/skill identifiers as securities.
    This helper is deliberately syntactic and network-free so migrations can
    use it safely while the database is opening.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    raw = symbol.strip().upper()
    if raw in _NON_SECURITY_SYMBOLS or "_" in raw or len(raw) > 32:
        return None
    if detect_market(raw) == "cn_a":
        return normalize_cn_display(raw)
    if not is_yahoo_safe(raw):
        return None
    return raw

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
    governance_status TEXT NOT NULL DEFAULT 'approved',
    expires_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_lessons_active ON strategy_lessons(active);
CREATE INDEX IF NOT EXISTS idx_strategy_lessons_type ON strategy_lessons(lesson_type);
CREATE INDEX IF NOT EXISTS idx_strategy_lessons_scope ON strategy_lessons(scope, target);

CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT,
    entry_zone TEXT NOT NULL DEFAULT '[]',
    stop_loss REAL,
    targets TEXT NOT NULL DEFAULT '[]',
    position_pct REAL,
    conditions TEXT NOT NULL DEFAULT '[]',
    rating TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'analysis',
    artifact_id TEXT NOT NULL DEFAULT '',
    reflection_case_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    triggered_at TEXT,
    trigger_reason TEXT,
    last_checked_trade_date TEXT,
    last_checked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_plans_symbol ON plans(symbol);
CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);
CREATE INDEX IF NOT EXISTS idx_plans_source ON plans(source);
CREATE INDEX IF NOT EXISTS idx_plans_artifact ON plans(artifact_id);

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

CREATE TABLE IF NOT EXISTS risk_events (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'announcement',
    title TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    event_date TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_risk_events_symbol ON risk_events(symbol);
CREATE INDEX IF NOT EXISTS idx_risk_events_status ON risk_events(status, level);
CREATE INDEX IF NOT EXISTS idx_risk_events_last_seen ON risk_events(last_seen_at);

CREATE TABLE IF NOT EXISTS decision_records (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_run_id TEXT NOT NULL DEFAULT '',
    source_artifact_id TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    name TEXT,
    decision_date TEXT NOT NULL,
    decision TEXT NOT NULL,
    horizon_days INTEGER NOT NULL DEFAULT 5,
    reference_price REAL,
    status TEXT NOT NULL DEFAULT 'open',
    reflection_case_id TEXT NOT NULL DEFAULT '',
    audit_last_attempt_at TEXT,
    audit_next_retry_at TEXT,
    audit_failure_count INTEGER NOT NULL DEFAULT 0,
    audit_last_error TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_records_date ON decision_records(decision_date);
CREATE INDEX IF NOT EXISTS idx_decision_records_symbol ON decision_records(symbol);
CREATE INDEX IF NOT EXISTS idx_decision_records_status ON decision_records(status);

CREATE TABLE IF NOT EXISTS trade_executions (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    executed_at TEXT NOT NULL,
    realized_pnl REAL,
    source TEXT NOT NULL DEFAULT 'user_confirmed',
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_trade_executions_decision ON trade_executions(decision_id);
CREATE INDEX IF NOT EXISTS idx_trade_executions_symbol ON trade_executions(symbol, executed_at);

CREATE TABLE IF NOT EXISTS decision_outcomes (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    as_of_date TEXT NOT NULL,
    close_at_signal REAL,
    close_at_horizon REAL,
    actual_return REAL NOT NULL,
    benchmark_return REAL,
    excess_return REAL,
    source TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(decision_id, horizon_days)
);
CREATE INDEX IF NOT EXISTS idx_decision_outcomes_decision ON decision_outcomes(decision_id);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL DEFAULT '',
    strategy_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted',
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_status ON backtest_runs(status, updated_at);

CREATE TABLE IF NOT EXISTS scheduler_job_state (
    name TEXT PRIMARY KEY,
    last_scheduled_date TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    last_started_at TEXT,
    last_completed_at TEXT,
    error TEXT
);
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
            # Migration: add plan-monitoring check-stamp columns if missing
            for _col in ("last_checked_trade_date", "last_checked_at"):
                try:
                    conn.execute(f"SELECT {_col} FROM plans LIMIT 0")
                except sqlite3.OperationalError:
                    conn.execute(f"ALTER TABLE plans ADD COLUMN {_col} TEXT")
            # Audit retry metadata was added after the initial decision ledger.
            # Keep the migration additive so existing user databases upgrade in
            # place without rebuilding the ledger.
            for _col, _definition in (
                ("audit_last_attempt_at", "TEXT"),
                ("audit_next_retry_at", "TEXT"),
                ("audit_failure_count", "INTEGER NOT NULL DEFAULT 0"),
                ("audit_last_error", "TEXT"),
            ):
                try:
                    conn.execute(f"SELECT {_col} FROM decision_records LIMIT 0")
                except sqlite3.OperationalError:
                    conn.execute(
                        f"ALTER TABLE decision_records ADD COLUMN {_col} {_definition}"
                    )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_decision_records_audit_retry
                ON decision_records(status, audit_next_retry_at, audit_last_attempt_at)
                """
            )
            # Explicit lesson governance separates mined candidates from rules
            # that are allowed to influence future analyses. Existing
            # cross-symbol lessons predate statistical validation and are
            # conservatively demoted once during migration.
            lesson_governance_added = False
            try:
                conn.execute("SELECT governance_status FROM strategy_lessons LIMIT 0")
            except sqlite3.OperationalError:
                conn.execute(
                    "ALTER TABLE strategy_lessons ADD COLUMN governance_status "
                    "TEXT NOT NULL DEFAULT 'approved'"
                )
                lesson_governance_added = True
            if lesson_governance_added:
                conn.execute(
                    "UPDATE strategy_lessons SET governance_status = 'retired' "
                    "WHERE active = 0"
                )
                conn.execute(
                    "UPDATE strategy_lessons SET governance_status = 'candidate', active = 0 "
                    "WHERE lesson_type = 'cross_symbol_pattern'"
                )
            self._backfill_report_artifacts(conn)
            self._backfill_decision_records(conn)
            # One-time dedup of historical reflection_cases created before the
            # case_id scheme was stabilized (old ids embedded run_id, so the
            # same (source_type, symbol, signal_date) accumulated multiple rows).
            self._dedup_reflection_cases(conn)

    def _backfill_decision_records(self, conn: sqlite3.Connection) -> None:
        """Idempotently enroll pre-ledger signals and stock reports for audit."""
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO decision_records (
                id, source_type, source_run_id, symbol, name, decision_date,
                decision, horizon_days, reference_price, status,
                reflection_case_id, payload_json, created_at, updated_at
            )
            SELECT 'signal:' || id, 'daily_pipeline', run_id, symbol, name,
                   trade_date, signal, 5, NULL, 'open',
                   'daily_pipeline:' || trade_date || ':' || UPPER(symbol),
                   payload, created_at, ?
            FROM signals
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO decision_records (
                id, source_type, source_run_id, source_artifact_id, symbol, name,
                decision_date, decision, horizon_days, reference_price, status,
                reflection_case_id, payload_json, created_at, updated_at
            )
            SELECT 'report:' || id, 'stock_analysis', run_id, id, ticker,
                   COALESCE(ticker_name, ticker), SUBSTR(created_at, 1, 10),
                   UPPER(COALESCE(rating, 'HOLD')), 5, NULL, 'open',
                   'stock_analysis:' || SUBSTR(created_at, 1, 10) || ':' || UPPER(ticker),
                   '{}', created_at, ?
            FROM reports
            WHERE UPPER(COALESCE(rating, '')) IN (
                'BUY', 'SELL', 'HOLD', 'OVERWEIGHT', 'UNDERWEIGHT',
                'ADD', 'REDUCE', 'EXIT', 'WATCHLIST'
            )
            """,
            (now,),
        )
        # Remove rows created by an earlier ledger migration that admitted
        # parser fallbacks such as UNKNOWN. Never remove rows with outcomes or
        # executions, because those have become user-visible audit evidence.
        conn.execute(
            """
            DELETE FROM decision_records
            WHERE source_type = 'stock_analysis'
              AND decision NOT IN (
                  'BUY', 'SELL', 'HOLD', 'OVERWEIGHT', 'UNDERWEIGHT',
                  'ADD', 'REDUCE', 'EXIT', 'WATCHLIST'
              )
              AND NOT EXISTS (SELECT 1 FROM decision_outcomes o WHERE o.decision_id = decision_records.id)
              AND NOT EXISTS (SELECT 1 FROM trade_executions e WHERE e.decision_id = decision_records.id)
            """
        )
        # Normalize legacy symbols and quarantine parser/report labels that were
        # accidentally admitted as tickers (for example ``DAILY_PIPELINE``).
        # Evidence-bearing rows are never deleted; they are marked cancelled so
        # historical outcomes/executions remain inspectable.
        rows = conn.execute("SELECT id, symbol FROM decision_records").fetchall()
        for row in rows:
            normalized = _normalize_audit_symbol(row["symbol"])
            if normalized:
                if normalized != row["symbol"]:
                    conn.execute(
                        "UPDATE decision_records SET symbol = ?, updated_at = ? WHERE id = ?",
                        (normalized, now, row["id"]),
                    )
                continue
            has_evidence = conn.execute(
                """
                SELECT EXISTS(SELECT 1 FROM decision_outcomes WHERE decision_id = ?)
                     + EXISTS(SELECT 1 FROM trade_executions WHERE decision_id = ?)
                """,
                (row["id"], row["id"]),
            ).fetchone()[0]
            if has_evidence:
                conn.execute(
                    """
                    UPDATE decision_records
                    SET status = 'cancelled', audit_last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("invalid_symbol_quarantined", now, row["id"]),
                )
            else:
                conn.execute("DELETE FROM decision_records WHERE id = ?", (row["id"],))

    def _dedup_reflection_cases(self, conn: sqlite3.Connection) -> None:
        """Delete duplicate reflection cases, keeping the newest per group.

        Groups by (source_type, symbol, signal_date) and keeps the row with the
        latest ``updated_at`` (ties broken by latest ``created_at``). Idempotent:
        a clean table has no duplicates and the DELETE matches nothing.
        """
        try:
            conn.execute(
                """
                DELETE FROM reflection_cases
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY source_type, symbol, signal_date
                                   ORDER BY updated_at DESC, created_at DESC
                               ) AS rn
                        FROM reflection_cases
                    ) WHERE rn = 1
                )
                """,
            )
        except sqlite3.OperationalError as exc:
            # Older SQLite lacks window functions; fall back to a NOT EXISTS
            # self-join that keeps the newest row per group.
            try:
                conn.execute(
                    """
                    DELETE FROM reflection_cases
                    WHERE EXISTS (
                        SELECT 1 FROM reflection_cases AS r2
                        WHERE r2.source_type = reflection_cases.source_type
                          AND r2.symbol = reflection_cases.symbol
                          AND r2.signal_date = reflection_cases.signal_date
                          AND (
                              r2.updated_at > reflection_cases.updated_at
                              OR (r2.updated_at = reflection_cases.updated_at
                                  AND r2.created_at > reflection_cases.created_at)
                          )
                    )
                    """,
                )
            except sqlite3.OperationalError:
                logger.debug("reflection_cases dedup skipped: %s", exc)

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

    def upsert_risk_event(
        self,
        *,
        event_id: str,
        symbol: str,
        level: str,
        title: str,
        name: str | None = None,
        event_type: str = "announcement",
        source: str = "",
        event_date: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict:
        """Insert a risk event or refresh its last-seen timestamp without duplicating it."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO risk_events (
                    id, symbol, name, level, event_type, title, source, event_date,
                    status, first_seen_at, last_seen_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    level = excluded.level,
                    title = excluded.title,
                    source = excluded.source,
                    event_date = excluded.event_date,
                    last_seen_at = excluded.last_seen_at,
                    payload_json = excluded.payload_json,
                    status = CASE WHEN risk_events.status = 'resolved' THEN 'open' ELSE risk_events.status END,
                    resolved_at = CASE WHEN risk_events.status = 'resolved' THEN NULL ELSE risk_events.resolved_at END
                """,
                (
                    event_id,
                    symbol.upper(),
                    name,
                    level,
                    event_type,
                    title,
                    source,
                    event_date,
                    now,
                    now,
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                ),
            )
        return self.get_risk_event(event_id) or {}

    def get_risk_event(self, event_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM risk_events WHERE id = ?", (event_id,)).fetchone()
        return self._decode_risk_event(dict(row)) if row else None

    def list_risk_events(
        self,
        *,
        status: str | None = "open",
        symbol: str | None = None,
        level: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            clauses.append("status = ?")
            values.append(status)
        if symbol:
            clauses.append("symbol = ?")
            values.append(symbol.upper())
        if level:
            clauses.append("level = ?")
            values.append(level)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(min(max(limit, 1), 500))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM risk_events {where} ORDER BY last_seen_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._decode_risk_event(dict(row)) for row in rows]

    def update_risk_event_status(self, event_id: str, status: str) -> dict | None:
        if status not in {"open", "acknowledged", "monitoring", "resolved"}:
            raise ValueError("invalid risk event status")
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE risk_events SET status = ?, resolved_at = ?, last_seen_at = last_seen_at WHERE id = ?",
                (status, now if status == "resolved" else None, event_id),
            )
        return self.get_risk_event(event_id)

    @staticmethod
    def _decode_risk_event(row: dict) -> dict:
        try:
            row["payload"] = json.loads(row.pop("payload_json", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            row["payload"] = {}
        return row

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

    def reconcile_interrupted_runs(self) -> int:
        """Fail persisted non-terminal runs left behind by a prior process."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM runs WHERE status IN ('pending', 'running')"
            ).fetchall()
            for row in rows:
                run_id = str(row["id"])
                message = "Interrupted by application restart"
                conn.execute(
                    "UPDATE runs SET status = 'failed', error = ?, completed_at = ? "
                    "WHERE id = ?",
                    (message, now, run_id),
                )
                seq = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                conn.execute(
                    "INSERT OR IGNORE INTO run_events "
                    "(run_id, seq, event_type, payload_json, created_at) "
                    "VALUES (?, ?, 'error', ?, ?)",
                    (run_id, seq, json.dumps({"message": message}), now),
                )
            return len(rows)

    def get_scheduler_job_state(self, name: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM scheduler_job_state WHERE name = ?", (name,)
            ).fetchone()
        return dict(row) if row else None

    def save_scheduler_job_state(
        self, name: str, *, scheduled_date: str | None, status: str,
        started_at: str | None = None, completed_at: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_job_state (
                    name, last_scheduled_date, status, last_started_at,
                    last_completed_at, error
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    last_scheduled_date = COALESCE(excluded.last_scheduled_date, last_scheduled_date),
                    status = excluded.status,
                    last_started_at = COALESCE(excluded.last_started_at, last_started_at),
                    last_completed_at = COALESCE(excluded.last_completed_at, last_completed_at),
                    error = excluded.error
                """,
                (name, scheduled_date, status, started_at, completed_at, error),
            )

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
        # Every production signal enters the audit ledger under the same stable
        # id, so reruns update evidence instead of duplicating performance data.
        reference_price = payload.get("latest_price") or payload.get("close") or payload.get("price")
        self.upsert_decision_record(
            decision_id=f"signal:{signal_id}",
            source_type="daily_pipeline",
            source_run_id=run_id,
            symbol=symbol,
            name=name,
            decision_date=str(payload.get("decision_target_date") or trade_date),
            decision=signal,
            horizon_days=int(payload.get("horizon_days") or 5),
            reference_price=float(reference_price) if isinstance(reference_price, (int, float)) else None,
            payload=payload,
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
    # Decision audit ledger
    # ------------------------------------------------------------------

    def upsert_decision_record(self, *, decision_id: str, source_type: str,
                               symbol: str, decision_date: str, decision: str,
                               horizon_days: int = 5, source_run_id: str = "",
                               source_artifact_id: str = "", name: str | None = None,
                               reference_price: float | None = None,
                               payload: dict | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        normalized_symbol = _normalize_audit_symbol(symbol)
        if normalized_symbol is None:
            raise ValueError(f"invalid auditable security symbol: {symbol!r}")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO decision_records (
                    id, source_type, source_run_id, source_artifact_id, symbol, name,
                    decision_date, decision, horizon_days, reference_price, status,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, decision=excluded.decision,
                    horizon_days=excluded.horizon_days,
                    reference_price=COALESCE(excluded.reference_price, decision_records.reference_price),
                    payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (decision_id, source_type, source_run_id, source_artifact_id,
                 normalized_symbol, name, decision_date, decision.upper(),
                 max(1, int(horizon_days)), reference_price,
                 json.dumps(payload or {}, ensure_ascii=False, default=str), now, now),
            )
        return self.get_decision_record(decision_id) or {}

    def get_decision_record(self, decision_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM decision_records WHERE id = ?", (decision_id,)).fetchone()
        return self._decode_json_columns(dict(row), ("payload_json",)) if row else None

    def list_decision_records(self, *, status: str | None = None,
                              symbol: str | None = None, limit: int = 100,
                              oldest_first: bool = False) -> list[dict]:
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            clauses.append("d.status = ?")
            values.append(status)
        if symbol:
            clauses.append("d.symbol = ?")
            values.append(symbol.upper())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(min(max(limit, 1), 500))
        order = "ASC" if oldest_first else "DESC"
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT d.*,
                    (SELECT COUNT(*) FROM trade_executions e WHERE e.decision_id=d.id) AS execution_count,
                    (SELECT COUNT(*) FROM decision_outcomes o WHERE o.decision_id=d.id) AS outcome_count,
                    (SELECT actual_return FROM decision_outcomes o
                     WHERE o.decision_id=d.id AND o.horizon_days=d.horizon_days) AS final_return,
                    (SELECT excess_return FROM decision_outcomes o
                     WHERE o.decision_id=d.id AND o.horizon_days=d.horizon_days) AS final_excess_return
                    FROM decision_records d {where}
                    ORDER BY d.decision_date {order}, d.created_at {order} LIMIT ?""",
                values,
            ).fetchall()
        return [self._decode_json_columns(dict(row), ("payload_json",)) for row in rows]

    def list_due_decision_records(self, *, limit: int = 100,
                                  as_of: str | None = None) -> list[dict]:
        """Return a fair audit batch without permanent head-of-line blocking.

        Rows that failed market-data retrieval are backed off through
        ``audit_next_retry_at``. Ordering by the least-recent attempt lets fresh
        decisions progress even when an old symbol remains temporarily
        unavailable.
        """
        now = as_of or datetime.now(timezone.utc).isoformat()
        bounded_limit = min(max(int(limit), 1), 500)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT d.*,
                    (SELECT COUNT(*) FROM trade_executions e WHERE e.decision_id=d.id)
                        AS execution_count,
                    (SELECT COUNT(*) FROM decision_outcomes o WHERE o.decision_id=d.id)
                        AS outcome_count
                FROM decision_records d
                WHERE d.status IN ('open', 'partially_realized', 'tracking')
                  AND (d.audit_next_retry_at IS NULL OR d.audit_next_retry_at <= ?)
                ORDER BY
                    CASE WHEN d.audit_last_attempt_at IS NULL THEN 0 ELSE 1 END,
                    d.audit_last_attempt_at ASC,
                    d.decision_date ASC,
                    d.created_at ASC
                LIMIT ?
                """,
                (now, bounded_limit),
            ).fetchall()
        return [self._decode_json_columns(dict(row), ("payload_json",)) for row in rows]

    def record_decision_audit_attempt(self, decision_id: str, *,
                                      error: str | None = None) -> dict | None:
        """Persist audit retry state, applying bounded exponential backoff."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT audit_failure_count FROM decision_records WHERE id = ?",
                (decision_id,),
            ).fetchone()
            if row is None:
                return None
            if error:
                failure_count = int(row["audit_failure_count"] or 0) + 1
                retry_days = min(2 ** max(failure_count - 1, 0), 7)
                next_retry = (now_dt + timedelta(days=retry_days)).isoformat()
            else:
                failure_count = 0
                next_retry = None
            conn.execute(
                """
                UPDATE decision_records
                SET audit_last_attempt_at = ?, audit_next_retry_at = ?,
                    audit_failure_count = ?, audit_last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, next_retry, failure_count, error, now, decision_id),
            )
        return self.get_decision_record(decision_id)

    def update_decision_record(self, decision_id: str, *, status: str | None = None,
                               reflection_case_id: str | None = None) -> dict | None:
        sets = ["updated_at = ?"]
        values: list[Any] = [datetime.now(timezone.utc).isoformat()]
        if status is not None:
            if status not in {"open", "partially_realized", "tracking", "realized", "cancelled"}:
                raise ValueError("invalid decision status")
            sets.append("status = ?")
            values.append(status)
        if reflection_case_id is not None:
            sets.append("reflection_case_id = ?")
            values.append(reflection_case_id)
        values.append(decision_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE decision_records SET {', '.join(sets)} WHERE id = ?", values)
        return self.get_decision_record(decision_id)

    def save_trade_execution(self, *, execution_id: str, symbol: str, action: str,
                             quantity: float, price: float, executed_at: str,
                             decision_id: str = "", realized_pnl: float | None = None,
                             source: str = "user_confirmed", payload: dict | None = None) -> dict:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trade_executions
                (id, decision_id, symbol, action, quantity, price, executed_at,
                 realized_pnl, source, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (execution_id, decision_id, symbol.upper(), action.lower(), quantity, price,
                 executed_at, realized_pnl, source,
                 json.dumps(payload or {}, ensure_ascii=False, default=str)),
            )
        return self.get_trade_execution(execution_id) or {}

    def get_trade_execution(self, execution_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM trade_executions WHERE id = ?", (execution_id,)).fetchone()
        return self._decode_json_columns(dict(row), ("payload_json",)) if row else None

    def list_trade_executions(self, *, decision_id: str | None = None,
                              symbol: str | None = None, limit: int = 100) -> list[dict]:
        clauses, values = [], []
        if decision_id:
            clauses.append("decision_id = ?")
            values.append(decision_id)
        if symbol:
            clauses.append("symbol = ?")
            values.append(symbol.upper())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(min(max(limit, 1), 500))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM trade_executions {where} ORDER BY executed_at DESC LIMIT ?", values
            ).fetchall()
        return [self._decode_json_columns(dict(row), ("payload_json",)) for row in rows]

    def save_decision_outcome(self, *, outcome_id: str, decision_id: str,
                              horizon_days: int, as_of_date: str, actual_return: float,
                              close_at_signal: float | None = None,
                              close_at_horizon: float | None = None,
                              benchmark_return: float | None = None,
                              source: str = "", payload: dict | None = None) -> dict:
        excess = actual_return - benchmark_return if benchmark_return is not None else None
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO decision_outcomes
                (id, decision_id, horizon_days, as_of_date, close_at_signal,
                 close_at_horizon, actual_return, benchmark_return, excess_return,
                 source, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id, horizon_days) DO UPDATE SET
                    as_of_date=excluded.as_of_date, close_at_signal=excluded.close_at_signal,
                    close_at_horizon=excluded.close_at_horizon, actual_return=excluded.actual_return,
                    benchmark_return=excluded.benchmark_return, excess_return=excluded.excess_return,
                    source=excluded.source, payload_json=excluded.payload_json""",
                (outcome_id, decision_id, horizon_days, as_of_date, close_at_signal,
                 close_at_horizon, actual_return, benchmark_return, excess, source,
                 json.dumps(payload or {}, ensure_ascii=False, default=str),
                 datetime.now(timezone.utc).isoformat()),
            )
            row = conn.execute(
                "SELECT * FROM decision_outcomes WHERE decision_id=? AND horizon_days=?",
                (decision_id, horizon_days),
            ).fetchone()
        return self._decode_json_columns(dict(row), ("payload_json",))

    def list_decision_outcomes(self, *, decision_id: str | None = None,
                               limit: int = 500) -> list[dict]:
        where, values = "", []
        if decision_id:
            where = "WHERE decision_id = ?"
            values.append(decision_id)
        values.append(min(max(limit, 1), 5000))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM decision_outcomes {where} ORDER BY as_of_date DESC LIMIT ?", values
            ).fetchall()
        return [self._decode_json_columns(dict(row), ("payload_json",)) for row in rows]

    @staticmethod
    def _decode_json_columns(row: dict, columns: tuple[str, ...]) -> dict:
        for column in columns:
            target = column.removesuffix("_json")
            try:
                row[target] = json.loads(row.pop(column, "{}") or "{}")
            except (json.JSONDecodeError, TypeError):
                row[target] = {}
        return row

    def save_backtest_run(self, *, backtest_id: str, strategy_type: str,
                          start_date: str, end_date: str, config: dict,
                          job_id: str = "", status: str = "submitted",
                          result: dict | None = None, error: str | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO backtest_runs
                (id, job_id, strategy_type, status, start_date, end_date, config_json,
                 result_json, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET job_id=excluded.job_id,
                    status=excluded.status, result_json=excluded.result_json,
                    error=excluded.error, updated_at=excluded.updated_at""",
                (backtest_id, job_id, strategy_type, status, start_date, end_date,
                 json.dumps(config, ensure_ascii=False, default=str),
                 json.dumps(result or {}, ensure_ascii=False, default=str), error, now, now),
            )
        return self.get_backtest_run(backtest_id) or {}

    def get_backtest_run(self, backtest_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (backtest_id,)).fetchone()
        return self._decode_json_columns(dict(row), ("config_json", "result_json")) if row else None

    def list_backtest_runs(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        where, values = "", []
        if status:
            where = "WHERE status=?"
            values.append(status)
        values.append(min(max(limit, 1), 200))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM backtest_runs {where} ORDER BY created_at DESC LIMIT ?", values
            ).fetchall()
        return [self._decode_json_columns(dict(row), ("config_json", "result_json")) for row in rows]

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
        lookback_days: int | None = None,
        due_only: bool = False,
    ) -> list[dict]:
        """List reflection cases with decoded JSON payloads.

        Args:
            limit: Max number of cases to return.
            status: Filter by status (e.g. 'reflected', 'pending').
            symbol: Filter by stock symbol.
            reflection_scope: Filter by reflection scope.
            eligible_only: Only return cases eligible for strategy learning.
            lookback_days: Only return cases updated within the last N days.
            due_only: Only return cases whose horizon has elapsed
                (signal_date + horizon_days, in calendar days, <= today) and
                order oldest-first. The reflection batch uses this so it reaches
                due cases instead of stalling on the newest pending cases that
                aren't due yet. Calendar days is a conservative lower bound
                (trading-day due dates are always >= it), so no truly-due case
                is dropped; the caller still skips cases whose price outcome
                isn't available yet.
        """
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
        if lookback_days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
            clauses.append("updated_at >= ?")
            params.append(cutoff)
        if due_only:
            clauses.append("date(signal_date, '+' || horizon_days || ' days') <= date('now')")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        order = "signal_date ASC, created_at ASC" if due_only else "signal_date DESC, created_at DESC"
        query += f" ORDER BY {order} LIMIT ?"
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
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM reflection_cases WHERE id = ?", (case_id,)
            ).fetchone()
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

    def save_plan(
        self,
        plan_id: str,
        *,
        symbol: str,
        name: str | None = None,
        entry_zone: list[float] | None = None,
        stop_loss: float | None = None,
        targets: list[float] | None = None,
        position_pct: float | None = None,
        conditions: list[dict] | None = None,
        rating: str | None = None,
        status: str = "draft",
        source: str = "analysis",
        artifact_id: str = "",
        reflection_case_id: str = "",
    ) -> None:
        """Save or replace a trade plan (entry/stop/targets/conditions)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO plans (
                    id, symbol, name, entry_zone, stop_loss, targets, position_pct,
                    conditions, rating, status, source, artifact_id, reflection_case_id,
                    created_at, updated_at, triggered_at, trigger_reason,
                    last_checked_trade_date, last_checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    symbol.strip().upper(),
                    name,
                    json.dumps(entry_zone or [], ensure_ascii=False),
                    stop_loss,
                    json.dumps(targets or [], ensure_ascii=False),
                    position_pct,
                    json.dumps(conditions or [], ensure_ascii=False),
                    rating,
                    status,
                    source,
                    artifact_id or "",
                    reflection_case_id or "",
                    now,
                    now,
                    None,
                    None,
                    None,
                    None,
                ),
            )

    def update_plan(
        self,
        plan_id: str,
        *,
        status: str | None = None,
        triggered_at: str | None = None,
        trigger_reason: str | None = None,
        reflection_case_id: str | None = None,
        last_checked_at: str | None = None,
        last_checked_trade_date: str | None = None,
    ) -> None:
        """Update mutable plan fields (status / trigger / check stamps / link)."""
        sets: list[str] = ["updated_at = ?"]
        values: list[Any] = [datetime.now(timezone.utc).isoformat()]
        if status is not None:
            sets.append("status = ?")
            values.append(status)
        if triggered_at is not None:
            sets.append("triggered_at = ?")
            values.append(triggered_at)
        if trigger_reason is not None:
            sets.append("trigger_reason = ?")
            values.append(trigger_reason)
        if reflection_case_id is not None:
            sets.append("reflection_case_id = ?")
            values.append(reflection_case_id)
        if last_checked_at is not None:
            sets.append("last_checked_at = ?")
            values.append(last_checked_at)
        if last_checked_trade_date is not None:
            sets.append("last_checked_trade_date = ?")
            values.append(last_checked_trade_date)
        values.append(plan_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE plans SET {', '.join(sets)} WHERE id = ?", values)

    def list_plans(
        self,
        limit: int = 50,
        status: str | None = None,
        symbol: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM plans"
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.strip().upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_plan(dict(row)) for row in rows]

    def get_plan(self, plan_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return self._decode_plan(dict(row)) if row else None

    def delete_plan(self, plan_id: str) -> int:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            return int(cur.rowcount or 0)

    def _decode_plan(self, row: dict) -> dict:
        for key in ("entry_zone", "targets", "conditions"):
            try:
                row[key] = json.loads(row.get(key) or "[]")
            except (json.JSONDecodeError, TypeError):
                row[key] = []
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
        governance_status: str | None = None,
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
                    evidence_count, confidence, active, governance_status, expires_at, payload_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    governance_status or ("approved" if active else "candidate"),
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

    def get_strategy_lesson(self, lesson_id: str) -> dict | None:
        """Fetch a single strategy lesson by id with its decoded payload."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_lessons WHERE id = ?", (lesson_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["active"] = bool(item.get("active"))
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["payload"] = {}
        return item

    def update_strategy_lesson(self, lesson_id: str, **fields: Any) -> dict | None:
        """Incrementally update a strategy lesson's fields.

        Supports cumulative updates: when ``evidence_count`` is passed, it is
        ADDED to the existing value rather than replacing it. This allows
        cross-symbol pattern mining to accumulate evidence over time.

        Returns the updated lesson dict or None if not found.
        """
        existing = None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_lessons WHERE id = ?", (lesson_id,)
            ).fetchone()
            if row is None:
                return None
            existing = dict(row)

        # Accumulate evidence_count if provided
        if "evidence_count" in fields:
            current_count = int(existing.get("evidence_count", 1))
            fields["evidence_count"] = current_count + int(fields["evidence_count"])

        now = datetime.now(timezone.utc).isoformat()
        allowed = {
            "lesson_type", "scope", "target", "finding", "suggested_adjustment",
            "evidence_count", "confidence", "active", "governance_status",
            "expires_at", "payload",
        }
        sets: list[str] = []
        params: list[Any] = []
        for key in fields:
            if key not in allowed:
                continue
            val = fields[key]
            if key == "payload":
                val = json.dumps(val or {}, ensure_ascii=False)
                key = "payload_json"
            elif key == "active":
                val = 1 if val else 0
            sets.append(f"{key} = ?")
            params.append(val)
        sets.append("updated_at = ?")
        params.append(now)
        params.append(lesson_id)

        with self._conn() as conn:
            conn.execute(
                f"UPDATE strategy_lessons SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            row = conn.execute(
                "SELECT * FROM strategy_lessons WHERE id = ?", (lesson_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["active"] = bool(result.get("active"))
        try:
            result["payload"] = json.loads(result.pop("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            result["payload"] = {}
        return result

    def deactivate_strategy_lesson(self, lesson_id: str) -> bool:
        """Deactivate a strategy lesson (sets active=0).

        Returns True if a row was updated, False if the lesson was not found.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE strategy_lessons SET active = 0, governance_status = 'retired', "
                "updated_at = ? WHERE id = ?",
                (now, lesson_id),
            )
            return int(cur.rowcount or 0) > 0

    def approve_strategy_lesson(self, lesson_id: str) -> bool:
        """Manually approve a reviewed candidate for strategy injection."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE strategy_lessons SET active = 1, governance_status = 'approved', "
                "updated_at = ? WHERE id = ? AND governance_status IN ('candidate', 'validated')",
                (now, lesson_id),
            )
            return int(cur.rowcount or 0) > 0

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

    def get_prediction_scorecard(
        self, lookback_days: int = 90, min_samples: int = 5
    ) -> dict:
        """Aggregate reflected cases into a read-only prediction-quality scorecard.

        Reuses ``list_reflection_cases`` (JSON-decoded payloads) for the
        ``reflected`` cases within the lookback window, then delegates to the
        pure ``build_scorecard`` aggregator. The active profile's investment
        style selects the static ``STYLE_ALPHA`` prior for the advisory
        ``alpha_suggestion``. No schema changes, no side effects.
        """
        from tradingagents.core.prediction_metrics import build_scorecard
        from tradingagents.core.signal_fusion import STYLE_ALPHA

        cases = self.list_reflection_cases(
            status="reflected",
            lookback_days=lookback_days,
            limit=10000,
        )
        style = str(self.get_user_profile().get("investment_style") or "medium_term")
        alpha_prior = STYLE_ALPHA.get(style, STYLE_ALPHA["medium_term"])
        return build_scorecard(
            cases,
            lookback_days=lookback_days,
            min_samples=min_samples,
            alpha_prior=alpha_prior,
            style=style,
        )
