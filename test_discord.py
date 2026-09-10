"""Send one test message, to prove the webhook works before deploying."""

import sys

import config
import notifier

if not config.DISCORD_WEBHOOK_URL:
    print("DISCORD_WEBHOOK_URL is not set in .env")
    sys.exit(2)

ok = notifier.notify_tasks(
    [{"id": "test-1", "title": "Test task", "detail": "If you can read this, Discord is wired up."}],
    total_open=1,
)
print("Sent." if ok else "Failed - check the webhook URL.")
sys.exit(0 if ok else 1)
