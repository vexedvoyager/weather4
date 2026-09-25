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
    threshold_description TEXT,
    raw_model_prob REAL
);

CREATE TABLE IF NOT EXISTS shadow_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    city TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('yes', 'no')),
    raw_prob REAL NOT NULL,
    clamped_prob REAL NOT NULL,
    price_cents INTEGER NOT NULL,
    threshold_description TEXT,
    composite_edge_score REAL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'settled')),
    outcome TEXT CHECK(outcome IN ('yes', 'no', 'void', NULL)),
    would_have_pnl_cents INTEGER,
    detected_at TEXT NOT NULL,
    settled_at TEXT
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
        ("forecast_cache", "raw_model_prob", "REAL"),
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


def get_open_ticker_set(db_path: str) -> set:
    """
    Returns the set of tickers currently open or partially filled.

    Added after a real incident where the bot bought the SAME contract
    twice on separate Price Check runs, minutes apart - max_positions_per_city
    only limited how many total positions a city could have, it never
    checked whether a specific ticker was already held. This closes that
    gap: before opening any new trade, its ticker is checked against this
    set first.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM trades WHERE status IN ('open', 'partially_filled')"
        ).fetchall()
        return {row["ticker"] for row in rows}


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


def get_trades_opened_since(db_path: str, cutoff_iso: str) -> list:
    """
    v5 FIX for a real reporting gap: the daily summary used to match
    opened_at against a calendar-day string prefix (LIKE 'YYYY-MM-DD%').
    Since Daily Summary runs once per day at a fixed time, any trade
    opened AFTER that snapshot but still the same UTC calendar day was
    never captured by ANY day's summary - confirmed by cross-referencing
    15 days of real summaries against settled trades, finding at least 7
    real trades that never appeared in any "opened" list. This uses a
    rolling window from an exact timestamp instead, so nothing falls
    through a calendar-day boundary gap.
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE opened_at >= ? ORDER BY opened_at",
            (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_trades_settled_since(db_path: str, cutoff_iso: str) -> list:
    """See get_trades_opened_since() - same rolling-window fix, applied
    to settlement reporting too."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE settled_at >= ? ORDER BY settled_at",
            (cutoff_iso,),
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
    threshold_description: str = None, raw_model_prob: float = None,
):
    """
    model_prob: the CLAMPED probability (see src/probability.py) - this is
        what drives real trading decisions.
    raw_model_prob: the unclamped computation, kept alongside for
        shadow-tracking (see src/price_check.py) - lets us compare what
        the bot WOULD have done under the old, unclamped logic against
        what it actually does now, without spending real budget.
    """
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO forecast_cache
                (ticker, city, model_prob, nbm_run_id, cached_at,
                 threshold_description, raw_model_prob)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                model_prob = excluded.model_prob,
                nbm_run_id = excluded.nbm_run_id,
                cached_at = excluded.cached_at,
                threshold_description = excluded.threshold_description,
                raw_model_prob = excluded.raw_model_prob
            """,
            (ticker, city, model_prob, nbm_run_id,
             datetime.now(timezone.utc).isoformat(), threshold_description, raw_model_prob),
        )
        conn.commit()


def get_cached_forecast(db_path: str, ticker: str, max_age_hours: float) -> dict | None:
    """Returns {"model_prob": float, "nbm_run_id": str, "cached_at": str,
    "threshold_description": str|None, "raw_model_prob": float|None} if a
    fresh-enough cached forecast exists for this ticker, else None."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT model_prob, nbm_run_id, cached_at, threshold_description, raw_model_prob "
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
        "raw_model_prob": row["raw_model_prob"],
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


# --- Shadow trades (v5) -----------------------------------------------------
# Candidates the confidence floor/ceiling (src/probability.py) excluded from
# real trading, but that the OLD unclamped logic would have traded. Tracked
# with zero real budget spent, so the floor/ceiling choice can be validated
# against real settlement outcomes over time - see IMPROVEMENTS.md item #10.

def has_pending_shadow_trade(db_path: str, ticker: str) -> bool:
    """Prevents re-logging the same still-eligible shadow candidate on
    every 5-minute Price Check run - only the first detection is recorded."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM shadow_trades WHERE ticker = ? AND status = 'pending'",
            (ticker,),
        ).fetchone()
        return row is not None


def insert_shadow_trade(
    db_path: str, ticker: str, city: str, side: str, raw_prob: float,
    clamped_prob: float, price_cents: int, threshold_description: str = None,
    composite_edge_score: float = None,
) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO shadow_trades
                (ticker, city, side, raw_prob, clamped_prob, price_cents,
                 threshold_description, composite_edge_score, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, city, side, raw_prob, clamped_prob, price_cents,
             threshold_description, composite_edge_score,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def get_pending_shadow_trades(db_path: str) -> list:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM shadow_trades WHERE status = 'pending'"
        ).fetchall()
        return [dict(r) for r in rows]


def settle_shadow_trade(db_path: str, shadow_id: int, outcome: str, would_have_pnl_cents: int):
    assert outcome in ("yes", "no", "void"), f"invalid outcome: {outcome}"
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE shadow_trades
            SET outcome = ?, would_have_pnl_cents = ?, status = 'settled', settled_at = ?
            WHERE id = ?
            """,
            (outcome, would_have_pnl_cents, datetime.now(timezone.utc).isoformat(), shadow_id),
        )
        conn.commit()


def get_shadow_trades_settled_on(db_path: str, date_str: str) -> list:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM shadow_trades WHERE status = 'settled' AND settled_at LIKE ?",
            (f"{date_str}%",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_shadow_trades_settled_since(db_path: str, cutoff_iso: str) -> list:
    """Rolling-window version of get_shadow_trades_settled_on(), matching
    the same fix applied to real trades (see get_trades_settled_since) -
    for consistency, since shadow trades are reported alongside real ones."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM shadow_trades WHERE status = 'settled' AND settled_at >= ?",
            (cutoff_iso,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_shadow_trade_summary(db_path: str) -> dict:
    """
    All-time tally of settled shadow trades: how many would have won vs.
    lost, and net would-have P&L - the evidence needed to eventually
    answer "was the floor/ceiling too conservative?"
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as n,
                COALESCE(SUM(would_have_pnl_cents), 0) as total_pnl_cents,
                SUM(CASE WHEN would_have_pnl_cents > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN would_have_pnl_cents < 0 THEN 1 ELSE 0 END) as losses
            FROM shadow_trades WHERE status = 'settled'
            """
        ).fetchone()
        return dict(row)
