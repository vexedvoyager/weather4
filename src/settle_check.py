"""
Settlement checker: the piece that was missing entirely through v1-v3.

settle_trade() in src/db.py was built and tested from the start, but
nothing ever CALLED it against real Kalshi data - there was no code that
asked "has this open position actually resolved yet?" This meant every
position, once opened, stayed 'open' forever regardless of what actually
happened, and Net P&L always showed $0.00 even when real (simulated)
wins and losses had occurred days earlier. Found by the user asking why
their daily summary showed no P&L despite positions that should have
closed.

This checks every open/partially-filled trade against Kalshi's current
market data. If a market has a real result now, the trade is settled:
P&L is computed from the trade's own recorded side/price/count (a simple
win-pays-$1/loss-pays-$0 model - see _compute_pnl_cents for the honest
caveat about Kalshi fees not being modeled here).

v5 ADDITION: also settles pending shadow trades (see src/price_check.py
and IMPROVEMENTS.md item #10) - candidates the confidence floor/ceiling
excluded from real trading, tracked with zero budget so the fix itself
can be validated against real outcomes. Shadow trades don't have a real
position size, so their would-have P&L is reported PER CONTRACT (as if
exactly 1 contract had been bought at the recorded price) - a comparable,
if simplified, basis across trades regardless of what hypothetical
sizing might have applied.

Run this on a schedule - wired into price_check.py so settlement gets
checked on the same cadence as everything else, without needing a
separate workflow.
"""
import logging
from datetime import datetime, timezone

from src import alerts, db
from src.config import load_config, resolve_path
from src.scan_common import build_client

logger = logging.getLogger("settle_check")


def _compute_pnl_cents(side: str, entry_price_cents: int, count: int, outcome: str) -> int:
    """
    Simple win-pays-$1.00/loss-pays-$0 model, matching how Kalshi's
    binary contracts actually pay out. Does NOT model Kalshi's real fee
    structure - no confirmed fee formula was available while building
    this, so real P&L (once live) will be slightly worse than what this
    reports. See KNOWN_LIMITATIONS.md.
    """
    if outcome == "void":
        return 0
    won = (side == "yes" and outcome == "yes") or (side == "no" and outcome == "no")
    if won:
        return count * (100 - entry_price_cents)
    else:
        return -count * entry_price_cents


def _fetch_market_result(client, ticker: str):
    """
    Returns (result_value, error) - result_value is None if not yet
    resolved or the fetch failed; error is the exception if the fetch
    itself failed (distinct from "not yet resolved", which is a
    successful fetch with an empty result field).
    """
    try:
        market = client.get_market(ticker)
        return market.get("result"), None
    except Exception as e:
        return None, e


def run_settle_check(cfg: dict, client=None) -> dict:
    """
    Checks every open trade (and every pending shadow trade) against
    Kalshi's current market data and settles anything that has a real
    result now.

    Returns a summary dict: {"checked": N, "settled": N, "still_open": N,
    "unrecognized_result": N, "shadow_checked": N, "shadow_settled": N}
    for logging/testing.
    """
    db_path = str(resolve_path(cfg, "database"))
    db.init_db(db_path)

    if client is None:
        client = build_client()

    open_trades = db.get_open_trade_rows(db_path)
    summary = {
        "checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0,
        "shadow_checked": 0, "shadow_settled": 0,
    }

    for trade in open_trades:
        summary["checked"] += 1
        ticker = trade["ticker"]

        result, error = _fetch_market_result(client, ticker)
        if error is not None:
            logger.warning("settle_check: could not fetch market for ticker=%s: %s", ticker, error)
            summary["still_open"] += 1
            continue

        if result in ("yes", "no", "void"):
            pnl_cents = _compute_pnl_cents(trade["side"], trade["entry_price_cents"], trade["count"], result)
            db.settle_trade(db_path, trade["id"], outcome=result, pnl_cents=pnl_cents)
            summary["settled"] += 1
            logger.info(
                "settle_check SETTLED id=%d ticker=%s side=%s outcome=%s pnl_cents=%d",
                trade["id"], ticker, trade["side"], result, pnl_cents,
            )
        elif result in (None, ""):
            summary["still_open"] += 1
            # Item #13: per-ticker diagnostic logging, so a genuinely
            # stuck position (vs. one just normally awaiting Kalshi's
            # settlement) is diagnosable from the log alone, matching the
            # pattern already used in forecast_refresh.py's rejection
            # breakdown.
            opened_at = datetime.fromisoformat(trade["opened_at"])
            age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
            logger.info(
                "still_open ticker=%s age=%.0fh raw_result=%r",
                ticker, age_hours, result,
            )
        else:
            # A real, unrecognized result value - e.g. the "last fair
            # price" exchange-discretion case documented in Kalshi's
            # official rulebook (see IMPROVEMENTS.md item #6). Don't
            # guess how to score it - flag loudly instead.
            summary["unrecognized_result"] += 1
            msg = (
                f"Market {ticker} (trade id {trade['id']}) has an unrecognized "
                f"result value: {result!r}. This doesn't match 'yes'/'no'/'void' - "
                f"possibly Kalshi's 'last fair price' exchange-discretion outcome. "
                f"NOT auto-settled; needs manual review."
            )
            logger.warning(msg)
            alerts.write_alert(cfg, "Unrecognized settlement result", msg)

    # --- Shadow trades (v5) ---------------------------------------------------
    pending_shadows = db.get_pending_shadow_trades(db_path)
    for shadow in pending_shadows:
        summary["shadow_checked"] += 1
        ticker = shadow["ticker"]

        result, error = _fetch_market_result(client, ticker)
        if error is not None:
            logger.debug("settle_check: could not fetch market for shadow ticker=%s: %s", ticker, error)
            continue

        if result in ("yes", "no", "void"):
            would_have_pnl_cents = _compute_pnl_cents(
                shadow["side"], shadow["price_cents"], count=1, outcome=result
            )
            db.settle_shadow_trade(db_path, shadow["id"], outcome=result, would_have_pnl_cents=would_have_pnl_cents)
            summary["shadow_settled"] += 1
            logger.info(
                "settle_check SHADOW SETTLED id=%d ticker=%s side=%s outcome=%s "
                "would_have_pnl_cents=%d (per contract) - this is what the OLD "
                "unclamped logic would have done",
                shadow["id"], ticker, shadow["side"], result, would_have_pnl_cents,
            )
        # Unrecognized/still-open shadow results are left pending silently -
        # these are hypothetical trades, not real positions, so they don't
        # need the same loud alerting a real unrecognized result would.

    logger.info(
        "Settlement check complete. checked=%d settled=%d still_open=%d unrecognized=%d "
        "shadow_checked=%d shadow_settled=%d",
        summary["checked"], summary["settled"], summary["still_open"], summary["unrecognized_result"],
        summary["shadow_checked"], summary["shadow_settled"],
    )
    return summary


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    cfg = load_config()
    run_settle_check(cfg)
