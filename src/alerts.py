"""
Writes urgent alerts to a file that .github/workflows/scan.yml checks
after every run. If present, the workflow opens a dedicated GitHub Issue
(separate from the daily summary issue), which triggers GitHub's normal
"new issue" notification email to anyone watching the repo - giving you
a same-run heads-up instead of waiting for the next morning's summary.

Only two conditions trigger this, on purpose: a position mismatch between
the local database and Kalshi's live account, and the daily loss limit
being hit. These are the two things worth interrupting your day for;
everything else belongs in the daily summary.
"""
from datetime import datetime, timezone
from pathlib import Path

from src.config import resolve_path


def _alert_path(cfg: dict) -> Path:
    # Lives alongside the database, in the same data/ folder that already
    # gets committed back to the repo by the GitHub Actions workflow.
    db_path = resolve_path(cfg, "database")
    return db_path.parent / "alerts" / "pending.txt"


def write_alert(cfg: dict, title: str, message: str):
    path = _alert_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = f"## {title}\n\n_{timestamp}_\n\n{message}\n\n---\n\n"
    # Append, in case multiple alerts fire in one run.
    with open(path, "a") as f:
        f.write(entry)


def has_pending_alerts(cfg: dict) -> bool:
    return _alert_path(cfg).exists()


def read_and_clear_alerts(cfg: dict) -> str:
    path = _alert_path(cfg)
    if not path.exists():
        return ""
    content = path.read_text()
    path.unlink()
    return content
