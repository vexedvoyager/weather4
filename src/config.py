"""
Loads config.yaml into a plain Python object. Nothing clever here on purpose —
if you ever need to check what a setting resolved to, print(cfg) will show you.
"""
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str = None) -> dict:
    config_path = Path(path) if path else REPO_ROOT / "config.yaml"
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    # Basic sanity checks so bad config fails loudly at startup, not silently
    # mid-run.
    assert cfg["mode"] in ("paper", "live"), (
        f"config.mode must be 'paper' or 'live', got {cfg['mode']!r}"
    )
    assert len(cfg["cities"]) > 0, "config.cities must not be empty"
    assert cfg["risk"]["total_budget_usd"] > 0, "total_budget_usd must be positive"
    assert cfg["risk"]["max_cost_per_trade_usd"] <= cfg["risk"]["total_budget_usd"], (
        "max_cost_per_trade_usd cannot exceed total_budget_usd"
    )

    return cfg


def resolve_path(cfg: dict, key: str) -> Path:
    """Resolve a path from cfg['paths'][key] relative to repo root."""
    return REPO_ROOT / cfg["paths"][key]
