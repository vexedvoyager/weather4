"""
SQLite trade ledger.

Known traps handled explicitly here:
  - settle_trade() updates the `status` column, not just outcome/pnl.
    A prior version of this kind of bot forgot this, and settled trades
    stayed 'open' forever, corrupting every downstream count.
  - Partial fills are tracked with their own status ('partially_filled'),
    distinct from 'accepted', so a query that only looks for 'accepted'
    doesn't silently skip partial fills that later settle for real money.
  - void outcomes are stored as their own outcome value, not coerced into
    a loss.
"""
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    city TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('yes', 'no')),
    count INTEGER NOT NULL,
    entry_price_cents INTEGER NOT NULL,
    forecast_prob REAL NOT NULL,
    composite_edge_score REAL NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('paper', 'live')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'partially_filled', 'settled', 'cancelled')),
    outcome TEXT CHECK(outcome IN ('yes', 'no', 'void', NULL)),
    pnl_cents INTEGER,
    fee_cents INTEGER DEFAULT 0,
    threshold_description TEXT,
    opened_at TEXT NOT NULL,
    settled_at TEXT
);

CREATE TABLE IF NOT EXISTS forecast_cache (
    ticker TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    model_prob REAL NOT NULL,
    nbm_run_id TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    threshold_description TEXT
);

CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    tickers_scanned INTEGER,
    tickers_eligible INTEGER,
    trades_opened INTEGER,
    db_open_count INTEGER,
    live_open_count INTEGER,
    position_mismatch INTEGER DEFAULT 0,
    notes TEXT
);
"""


@contextmanager
def get_connection(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str):
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate_add_missing_columns(conn)
        conn.commit()


def _migrate_add_missing_columns(conn):
    """
    Handles the case where a database created by an earlier version of
    this code already exists on disk with an older schema - SQLite's
    CREATE TABLE IF NOT EXISTS does nothing for a table that already
    exists, even if new columns were added to the schema since. Without
    this, a real live database (with real trades already in it) would
    break the first time newer code tries to read/write a column that
    doesn't exist yet on disk.
    """
    migrations = [
        ("trades", "threshold_description", "TEXT"),
        ("forecast_cache", "threshold_description", "TEXT"),
    ]
    for table, column, col_type in migrations:
        existing_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.info("db_migration added_column table=%s column=%s", table, column)


def insert_trade(
    db_path: str, ticker: str, city: str, side: str, count: int,
    entry_price_cents: int, forecast_prob: float, composite_edge_score: float,
    mode: str, status: str = "open", threshold_description: str = None,
) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO trades
                (ticker, city, side, count, entry_price_cents, forecast_prob,
                 composite_edge_score, mode, status, threshold_description, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, city, side, count, entry_price_cents, forecast_prob,
             composite_edge_score, mode, status, threshold_description,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def settle_trade(db_path: str, trade_id: int, outcome: str, pnl_cents: int, fee_cents: int = 0):
    """
    outcome must be one of 'yes', 'no', 'void'. void is NOT the same as a
    loss - it means the stake is returned, pnl_cents should reflect that
    (typically 0 or just -fee if any fee applied).
    """
    assert outcome in ("yes", "no", "void"), f"invalid outcome: {outcome}"
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE trades
            SET outcome = ?, pnl_cents = ?, fee_cents = ?,
                status = 'settled', settled_at = ?
            WHERE id = ?
            """,
            (outcome, pnl_cents, fee_cents, datetime.now(timezone.utc).isoformat(), trade_id),
        )
        conn.commit()
        logger.info(
            "trade_settled id=%d outcome=%s pnl_cents=%d fee_cents=%d",
            trade_id, outcome, pnl_cents, fee_cents,
        )


def count_open_trades(db_path: str, city: str = None) -> int:
    query = "SELECT COUNT(*) as n FROM trades WHERE status IN ('open', 'partially_filled')"
    params = ()
    if city:
        query += " AND city = ?"
        params = (city,)
    with get_connection(db_path) as conn:
        row = conn.execute(query, params).fetchone()
        return row["n"]


def total_deployed_cents(db_path: str) -> int:
    """Sum of cost basis for currently-open positions (not yet settled)."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(count * entry_price_cents), 0) as total
            FROM trades WHERE status IN ('open', 'partially_filled')
            """
        ).fetchone()
        return row["total"]


def get_open_trade_rows(db_path: str) -> list:
    """
    Returns full rows for every currently-open (or partially-filled)
    trade - used by settle_check.py to know what to ask Kalshi about.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status IN ('open', 'partially_filled')"
        ).fetchall()
        return [dict(r) for r in rows]


def daily_pnl_cents(db_path: str, date_str: str) -> int:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(pnl_cents), 0) as total
            FROM trades
            WHERE status = 'settled' AND settled_at LIKE ?
            """,
            (f"{date_str}%",),
        ).fetchone()
        return row["total"]


def upsert_forecast_cache(
    db_path: str, ticker: str, city: str, model_prob: float, nbm_run_id: str,
    threshold_description: str = None,
):
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO forecast_cache
                (ticker, city, model_prob, nbm_run_id, cached_at, threshold_description)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                model_prob = excluded.model_prob,
                nbm_run_id = excluded.nbm_run_id,
                cached_at = excluded.cached_at,
                threshold_description = excluded.threshold_description
            """,
            (ticker, city, model_prob, nbm_run_id,
             datetime.now(timezone.utc).isoformat(), threshold_description),
        )
        conn.commit()


def get_cached_forecast(db_path: str, ticker: str, max_age_hours: float) -> dict | None:
    """Returns {"model_prob": float, "nbm_run_id": str, "cached_at": str,
    "threshold_description": str|None} if a fresh-enough cached forecast
    exists for this ticker, else None."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT model_prob, nbm_run_id, cached_at, threshold_description "
            "FROM forecast_cache WHERE ticker = ?",
            (ticker,),
        ).fetchone()

    if row is None:
        return None

    cached_at = datetime.fromisoformat(row["cached_at"])
    age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None

    return {
        "model_prob": row["model_prob"], "nbm_run_id": row["nbm_run_id"],
        "cached_at": row["cached_at"], "threshold_description": row["threshold_description"],
    }


def clear_forecast_cache_for_city(db_path: str, city: str):
    """Called at the start of a forecast-refresh run for a city, so stale
    tickers (e.g. a market that closed) don't linger in the cache forever."""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM forecast_cache WHERE city = ?", (city,))
        conn.commit()


def get_todays_scan_totals(db_path: str, date_str: str) -> dict:
    """
    Sums up today's scan_log rows into the numbers that actually answer
    "is anything happening": total markets scanned, total eligible
    candidates found, and total trades opened, across all of today's
    scan runs. Added in v3 after a real incident where scans were
    running (nonzero count) but producing zero trades, and the daily
    summary had no way to show WHY - this closes that gap.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as scan_count,
                COALESCE(SUM(tickers_scanned), 0) as total_scanned,
                COALESCE(SUM(tickers_eligible), 0) as total_eligible,
                COALESCE(SUM(trades_opened), 0) as total_opened,
                COALESCE(SUM(position_mismatch), 0) as mismatch_count
            FROM scan_log
            WHERE run_at LIKE ?
            """,
            (f"{date_str}%",),
        ).fetchone()
        return dict(row)


def get_forecast_cache_coverage(db_path: str) -> dict:
    """
    Returns per-city counts of currently-cached forecasts, so a "zero
    trades" day can be distinguished at a glance: is Forecast Refresh
    actually populating the cache, or is Price Check correctly finding
    nothing to compare against because the cache is empty?
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT city, COUNT(*) as cached_count FROM forecast_cache GROUP BY city"
        ).fetchall()
        return {row["city"]: row["cached_count"] for row in rows}


def log_scan(
    db_path: str, tickers_scanned: int, tickers_eligible: int, trades_opened: int,
    db_open_count: int, live_open_count: int, notes: str = "",
):
    mismatch = 1 if abs(db_open_count - live_open_count) > 1 else 0
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO scan_log
                (run_at, tickers_scanned, tickers_eligible, trades_opened,
                 db_open_count, live_open_count, position_mismatch, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (datetime.now(timezone.utc).isoformat(), tickers_scanned, tickers_eligible,
             trades_opened, db_open_count, live_open_count, mismatch, notes),
        )
        conn.commit()
    if mismatch:
        logger.warning(
            "POSITION MISMATCH db_open=%d live_open=%d — investigate before trusting counts",
            db_open_count, live_open_count,
        )
