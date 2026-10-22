"""
v6.0 tests: fees (#5), version stamping/backfill (#18), forecast provenance
and settlement values (#21), market observations and shadow cities (#22).
"""
import json
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

from src import db
from src.fees import kalshi_fee_cents
from src.price_check import run_price_check
from src.settle_check import _compute_pnl_cents, run_settle_check
from src.version import BOT_VERSION


def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    db.init_db(path)
    return path


# ---------------------------------------------------------------- fees (#5)

def test_fee_known_values():
    assert kalshi_fee_cents(100, 50) == 175        # 0.07*100*.25 = $1.75
    assert kalshi_fee_cents(1, 50) == 2            # $0.0175 rounds UP to 2c
    assert kalshi_fee_cents(5, 85) == 5            # $0.0446 -> 5c
    assert kalshi_fee_cents(500, 1) == 35          # $0.3465 -> 35c
    assert kalshi_fee_cents(0, 50) == 0
    assert kalshi_fee_cents(10, 50, rate=0) == 0


def test_fee_is_symmetric_around_50c_and_peaks_there():
    assert kalshi_fee_cents(100, 30) == kalshi_fee_cents(100, 70)
    assert kalshi_fee_cents(100, 50) > kalshi_fee_cents(100, 10)


def test_fee_does_not_round_an_exact_cent_up_from_float_noise():
    # 0.07 * 100 * 0.5 * 0.5 = 1.75 exactly representable cases are fine; check
    # a case that lands exactly on a cent: 0.07*200*.5*.5 = 3.50 -> 350, not 351
    assert kalshi_fee_cents(200, 50) == 350


def test_pnl_subtracts_fee_on_win_and_loss_but_not_void():
    assert _compute_pnl_cents("yes", 60, 10, "yes", fee_cents=15) == 400 - 15
    assert _compute_pnl_cents("yes", 60, 10, "no", fee_cents=15) == -600 - 15
    assert _compute_pnl_cents("yes", 60, 10, "void", fee_cents=15) == 0
    # default fee 0 keeps pre-v6 and shadow-trade behavior unchanged
    assert _compute_pnl_cents("no", 68, 7, "no") == 224


# --------------------------------------------- version stamping / backfill (#18)

def test_new_trades_are_stamped_with_current_version():
    path = _temp_db()
    db.insert_trade(path, "T1", "Chicago", "yes", 1, 50, 0.6, 0.7, "paper")
    row = db.get_open_trade_rows(path)[0]
    assert row["bot_version"] == BOT_VERSION


def test_backfill_labels_old_rows_by_go_live_date_and_is_idempotent():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    # An OLD-schema database (no v6 columns), as the real repo has on disk.
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, city TEXT NOT NULL,
            side TEXT NOT NULL, count INTEGER NOT NULL, entry_price_cents INTEGER NOT NULL,
            forecast_prob REAL NOT NULL, composite_edge_score REAL NOT NULL, mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', outcome TEXT, pnl_cents INTEGER,
            fee_cents INTEGER DEFAULT 0, threshold_description TEXT,
            opened_at TEXT NOT NULL, settled_at TEXT);
        INSERT INTO trades (ticker,city,side,count,entry_price_cents,forecast_prob,composite_edge_score,mode,opened_at)
        VALUES ('A','Chicago','yes',1,50,0.5,0.7,'paper','2026-09-24T02:44:00+00:00'),
               ('B','Chicago','no',1,50,0.5,0.7,'paper','2026-09-25T01:45:00+00:00'),
               ('C','Chicago','no',1,50,0.5,0.7,'paper','2026-10-15T01:45:00+00:00');
    """)
    conn.commit()
    conn.close()

    db.init_db(path)   # migrates + backfills
    with db.get_connection(path) as c:
        versions = {r["ticker"]: r["bot_version"] for r in c.execute("SELECT ticker, bot_version FROM trades")}
    assert versions == {"A": "v4", "B": "v5", "C": "v5"}

    # A real stamp must never be overwritten on later runs.
    with db.get_connection(path) as c:
        c.execute("UPDATE trades SET bot_version = 'v6.0' WHERE ticker = 'C'")
        c.commit()
    db.init_db(path)
    with db.get_connection(path) as c:
        assert c.execute("SELECT bot_version FROM trades WHERE ticker='C'").fetchone()[0] == "v6.0"


def test_migration_preserves_existing_trade_data():
    path = _temp_db()
    tid = db.insert_trade(path, "T1", "Miami", "no", 3, 40, 0.2, 0.8, "paper")
    db.settle_trade(path, tid, "no", 180)
    db.init_db(path)   # second init must be harmless
    row = [r for r in _all_trades(path)][0]
    assert row["pnl_cents"] == 180 and row["outcome"] == "no"


def _all_trades(path):
    with db.get_connection(path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM trades")]


# ------------------------------------- price_check integration (#5, #21, #22)

def _cfg(path, shadow=None):
    cfg = {
        "mode": "paper",
        "paths": {"database": path},
        "risk": {"total_budget_usd": 50.0, "max_cost_per_trade_usd": 5.0,
                 "max_positions_per_city": 10, "daily_loss_limit_usd": 10.0},
        "operations": {"forecast_cache_max_age_hours": 8, "min_volume_contracts": 5},
        "edge": {"min_probability_gap": 0.08, "min_composite_score": 0.0,
                 "weights": {"spread": 0.3, "volume": 0.2, "imbalance": 0.25, "mispricing": 0.25}},
        "cities": [{"name": "Austin", "kalshi_series_prefix": "KXHIGHAUS"}],
        "fees": {"taker_rate": 0.07},
    }
    if shadow:
        cfg["shadow_cities"] = shadow
    return cfg


def _market(ticker):
    return {"ticker": ticker, "yes_bid_dollars": "0.50", "no_bid_dollars": "0.49",
            "volume_24h_fp": "100.00"}


def _client(series_to_tickers):
    client = MagicMock()
    client.get_markets_by_series.side_effect = lambda s, status="open": [
        _market(t) for t in series_to_tickers.get(s, [])]
    client.get_orderbook.return_value = {"yes_dollars": [["0.10", "50.00"]], "no_dollars": [["0.10", "50.00"]]}
    client.get_positions.return_value = []
    return client


def _run(cfg, client):
    with patch("src.price_check.build_client", return_value=client), \
         patch("src.price_check.run_settle_check",
               return_value={"checked": 0, "settled": 0, "still_open": 0, "unrecognized_result": 0}):
        run_price_check(cfg)


def test_trade_records_fee_version_and_forecast_provenance():
    path = _temp_db()
    ladder = json.dumps({"p10": 80, "p25": 83, "p50": 86, "p75": 89, "p90": 92})
    db.upsert_forecast_cache(
        path, "KXHIGHAUS-T1", "Austin", model_prob=0.95, nbm_run_id="2026-10-21T19Z",
        threshold_description="high > 80°F", raw_model_prob=0.99,
        nbm_forecast_hour=29, nbm_valid_utc="2026-10-23T00:00:00+00:00", ladder_json=ladder)
    _run(_cfg(path), _client({"KXHIGHAUS": ["KXHIGHAUS-T1"]}))

    trade = _all_trades(path)[0]
    assert trade["bot_version"] == BOT_VERSION
    assert trade["nbm_run_id"] == "2026-10-21T19Z"
    assert trade["nbm_forecast_hour"] == 29
    assert trade["nbm_valid_utc"].startswith("2026-10-23")
    assert json.loads(trade["ladder_json"])["p50"] == 86
    assert trade["fee_cents"] == kalshi_fee_cents(trade["count"], trade["entry_price_cents"])
    assert trade["fee_cents"] > 0


def test_observation_recorded_once_for_traded_and_shadow_cities_but_shadow_never_trades():
    path = _temp_db()
    for ticker, city in (("KXHIGHAUS-T1", "Austin"), ("KXHIGHDEN-T1", "Denver")):
        db.upsert_forecast_cache(path, ticker, city, model_prob=0.95, nbm_run_id="r",
                                 threshold_description="high > 80°F", raw_model_prob=0.99)
    shadow = [{"name": "Denver", "kalshi_series_prefix": "KXHIGHDEN",
               "expected_station": "KDEN", "timezone": "America/Denver"}]
    client = _client({"KXHIGHAUS": ["KXHIGHAUS-T1"], "KXHIGHDEN": ["KXHIGHDEN-T1"]})
    cfg = _cfg(path, shadow=shadow)
    _run(cfg, client)
    _run(cfg, client)   # second run must not duplicate observations

    with db.get_connection(path) as c:
        obs = {r["ticker"]: dict(r) for r in c.execute("SELECT * FROM market_observations")}
    assert set(obs) == {"KXHIGHAUS-T1", "KXHIGHDEN-T1"}
    assert obs["KXHIGHDEN-T1"]["is_shadow_city"] == 1 and obs["KXHIGHAUS-T1"]["is_shadow_city"] == 0
    assert obs["KXHIGHAUS-T1"]["market_yes_cents"] == 51          # 100 - best NO bid (49)
    assert obs["KXHIGHAUS-T1"]["bot_version"] == BOT_VERSION
    # Only the traded city may open a trade; the shadow city never does.
    traded_cities = {t["city"] for t in _all_trades(path)}
    assert "Denver" not in traded_cities


def test_observation_skipped_when_no_fresh_forecast():
    path = _temp_db()
    _run(_cfg(path), _client({"KXHIGHAUS": ["KXHIGHAUS-NOFORECAST"]}))
    with db.get_connection(path) as c:
        assert c.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0] == 0


# ----------------------------------- settle_check integration (#5, #21, #22)

def test_settle_applies_stored_fee_and_records_settlement_value():
    path = _temp_db()
    tid = db.insert_trade(path, "KXHIGHAUS-T1", "Austin", "no", 5, 85, 0.1, 0.85, "paper", fee_cents=5)
    client = MagicMock()
    client.get_market.return_value = {"result": "no", "expiration_value": "91"}
    run_settle_check({"paths": {"database": path}}, client=client)
    trade = _all_trades(path)[0]
    assert trade["status"] == "settled"
    assert trade["pnl_cents"] == 5 * 15 - 5          # win 15c each, minus 5c fee
    assert trade["fee_cents"] == 5
    assert trade["settlement_value"] == "91"


def test_settle_void_refunds_fee():
    path = _temp_db()
    db.insert_trade(path, "T1", "Austin", "no", 5, 85, 0.1, 0.85, "paper", fee_cents=5)
    client = MagicMock()
    client.get_market.return_value = {"result": "void"}
    run_settle_check({"paths": {"database": path}}, client=client)
    trade = _all_trades(path)[0]
    assert trade["pnl_cents"] == 0 and trade["fee_cents"] == 0


def test_settle_missing_expiration_value_is_stored_as_null():
    path = _temp_db()
    db.insert_trade(path, "T1", "Austin", "yes", 1, 50, 0.6, 0.7, "paper")
    client = MagicMock()
    client.get_market.return_value = {"result": "yes"}
    run_settle_check({"paths": {"database": path}}, client=client)
    assert _all_trades(path)[0]["settlement_value"] is None


def test_settle_check_settles_pending_observations():
    path = _temp_db()
    db.insert_market_observation(path, "OBS-1", "Denver", True, "high > 70°F", 0.7, 0.7, 40, 38, 100)
    client = MagicMock()
    client.get_market.return_value = {"result": "yes", "expiration_value": "72"}
    summary = run_settle_check({"paths": {"database": path}}, client=client)
    assert summary["observations_settled"] == 1
    settled = db.get_settled_market_observations(path)
    assert settled[0]["outcome"] == "yes" and settled[0]["settlement_value"] == "72"
    assert db.get_pending_market_observations(path) == []


# -------------------------------------- forecast_refresh shadow cities (#22)

def test_forecast_refresh_caches_shadow_city_with_provenance():
    from src.forecast_refresh import run_forecast_refresh

    path = _temp_db()
    fhrs = [23, 35]
    def row(label, vals):
        return f"{label:<6}" + "".join(str(v).rjust(4) for v in vals)
    bulletin = "\n".join([
        " KDEN    NBM V5.0 NBP GUIDANCE    9/03/2026  0100 UTC",
        row("UTC", [0, 12]), row("FHR", fhrs),
        row("TXNP1", [70, 45]), row("TXNP2", [73, 48]), row("TXNP5", [76, 51]),
        row("TXNP7", [79, 54]), row("TXNP9", [82, 57]),
    ])
    cfg = _cfg(path, shadow=[{"name": "Denver", "kalshi_series_prefix": "KXHIGHDEN",
                              "expected_station": "KDEN", "timezone": "America/Denver"}])
    cfg["cities"] = []   # only the shadow city, to isolate it
    cfg["probability_model"] = {"sigma_multiplier": 1.15, "min_forecast_hour": 24}
    market = {"ticker": "KXHIGHDEN-26SEP04-T75", "strike_type": "greater", "floor_strike": 75,
              "close_time": "2026-09-04T05:00:00Z"}   # 28h after the 01Z run -> nearest col FHR 23
    client = MagicMock()
    client.get_markets_by_series.return_value = [market]

    with patch("src.forecast_refresh.build_client", return_value=client), \
         patch("src.forecast_refresh.nbm.fetch_latest_bulletin", return_value=(bulletin, "2026-09-03T01Z")), \
         patch("src.forecast_refresh.verify_station", return_value=True):
        run_forecast_refresh(cfg)

    cached = db.get_cached_forecast(path, "KXHIGHDEN-26SEP04-T75", 8)
    assert cached is not None
    assert cached["nbm_forecast_hour"] == 23
    assert cached["nbm_valid_utc"].startswith("2026-09-04T00:00")
    assert json.loads(cached["ladder_json"])["p50"] == 76
