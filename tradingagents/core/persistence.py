"""SQLite persistence layer for run history and reports.

Stores run metadata and report content so the frontend can display
historical data without re-running analyses.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
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

CREATE INDEX IF NOT EXISTS idx_runs_skill ON runs(skill_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_reports_ticker ON reports(ticker);
CREATE INDEX IF NOT EXISTS idx_reports_run ON reports(run_id);
CREATE INDEX IF NOT EXISTS idx_signals_trade_date ON signals(trade_date);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
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

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

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
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO reports (id, run_id, ticker, ticker_name, rating, content, report_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, run_id, ticker, ticker_name, rating, content, path, datetime.now(timezone.utc).isoformat()),
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

    def list_runs(self, limit: int = 50) -> list[dict]:
        """List recent runs."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
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
