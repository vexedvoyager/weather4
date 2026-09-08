"""
Tiny CLI used by .github/workflows/scan.yml:

    python -m src.check_alerts
        prints alert content to stdout and exits 0 if there ARE pending
        alerts (workflow then creates a GitHub issue from the output)
        exits 1 if there are none (workflow skips the issue-creation step)

Clearing happens in a separate explicit step so the alert isn't lost if
the issue-creation step itself fails.
"""
import sys

from src.alerts import has_pending_alerts, read_and_clear_alerts
from src.config import load_config

if __name__ == "__main__":
    cfg = load_config()
    if not has_pending_alerts(cfg):
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--clear":
        content = read_and_clear_alerts(cfg)
    else:
        # Peek without clearing.
        from src.alerts import _alert_path
        content = _alert_path(cfg).read_text()

    print(content)
    sys.exit(0)
