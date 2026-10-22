"""
Single source of truth for the bot's version, stamped on every trade,
shadow trade and market observation (IMPROVEMENTS.md item #18) and printed
in the Daily Summary and dashboard headers.

Bump this whenever model or strategy behavior changes, so results can be
compared per version instead of blended together.
"""
BOT_VERSION = "v6.0"

# Go-live dates of earlier versions, used ONLY to backfill bot_version onto
# trades recorded before version stamping existed (see db.backfill_bot_version).
# Trades opened on/after V5_START are "v5"; earlier ones are "v4".
V5_START_ISO = "2026-09-25"
V6_START_ISO = "2026-10-22"
