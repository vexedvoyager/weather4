"""config.yaml sanity for v6.1: shadow cities are complete, unique, and never overlap traded cities."""
from zoneinfo import ZoneInfo

from src.config import load_config


def test_shadow_cities_are_complete_and_valid():
    cfg = load_config()
    shadow = cfg["shadow_cities"]
    assert len(shadow) >= 5
    for c in shadow:
        assert set(c) >= {"name", "kalshi_series_prefix", "expected_station", "timezone"}
        assert c["kalshi_series_prefix"].startswith("KXHIGH")
        assert len(c["expected_station"]) == 4 and c["expected_station"].startswith("K")
        ZoneInfo(c["timezone"])          # raises if not a real IANA zone
        assert "FILL_IN" not in str(c)


def test_shadow_cities_never_overlap_traded_cities():
    cfg = load_config()
    traded = cfg["cities"]
    for key in ("name", "kalshi_series_prefix", "expected_station"):
        assert not {c[key] for c in traded} & {c[key] for c in cfg["shadow_cities"]}


def test_shadow_cities_have_unique_names_and_series():
    shadow = load_config()["shadow_cities"]
    assert len({c["name"] for c in shadow}) == len(shadow)
    assert len({c["kalshi_series_prefix"] for c in shadow}) == len(shadow)
