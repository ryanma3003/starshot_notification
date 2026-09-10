"""Discord webhook delivery."""

import logging
import time

import requests

import config

log = logging.getLogger("notifier")

# Discord accepts at most 10 embeds per message.
MAX_EMBEDS = 10
COLOR_TASK = 0x2ECC71   # green
COLOR_ALERT = 0xE74C3C  # red


def _post(payload: dict, webhook: str = "", retries: int = 3) -> bool:
    """POST to the webhook, honouring Discord's rate limit. Never raises."""
    webhook = webhook or config.DISCORD_WEBHOOK_URL
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(webhook, json=payload, timeout=20)
        except requests.RequestException as exc:
            log.warning("Discord POST failed (attempt %d/%d): %s", attempt, retries, exc)
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 429:
            wait = 5.0
            try:
                wait = float(resp.json().get("retry_after", wait))
            except ValueError:
                pass
            log.warning("Rate limited by Discord, sleeping %.1fs", wait)
            time.sleep(wait + 0.5)
            continue

        if 200 <= resp.status_code < 300:
            return True

        log.warning("Discord returned %s: %s", resp.status_code, resp.text[:300])
        time.sleep(2 * attempt)

    log.error("Giving up on Discord message after %d attempts", retries)
    return False


def notify_tasks(tasks: list[dict], total_open: int, account: str = "",
                 webhook: str = "") -> bool:
    """Announce newly-appeared tasks. `tasks` are the new ones only."""
    if not tasks:
        return True

    content = config.DISCORD_MENTION or ""
    who = f" for **{account}**" if account else ""
    header = (f"**{len(tasks)} new task{'s' if len(tasks) != 1 else ''} "
              f"available**{who}")
    if total_open != len(tasks):
        header += f"  ·  {total_open} open in the queue"
    content = f"{content} {header}".strip()

    ok = True
    # Chunk so a big batch still gets through in full.
    for start in range(0, len(tasks), MAX_EMBEDS):
        chunk = tasks[start:start + MAX_EMBEDS]
        embeds = [
            {
                "title": (t.get("title") or "Task")[:250],
                "url": config.TARGET_URL,
                "description": (t.get("detail") or "")[:400] or None,
                "color": COLOR_TASK,
                "footer": {"text": f"id: {t.get('id', '?')}"},
            }
            for t in chunk
        ]
        # Only the first chunk carries the mention, so you get pinged once.
        payload = {"embeds": embeds}
        if start == 0:
            payload["content"] = content
        ok = _post(payload, webhook) and ok

    return ok


def notify_alert(title: str, detail: str, account: str = "",
                 webhook: str = "") -> bool:
    """Operational problem the user needs to act on (bad login, site changed, ...)."""
    who = f"[{account}] " if account else ""
    payload = {
        "content": config.DISCORD_MENTION or None,
        "embeds": [{
            "title": f"⚠️ {who}{title}"[:250],
            "description": detail[:1800],
            "color": COLOR_ALERT,
        }],
    }
    return _post(payload, webhook)


def notify_released(account: str, mode: str, webhook: str = "") -> bool:
    """Tells you the bot has stepped aside so you can work the task yourself."""
    if mode == "browser":
        detail = ("This account's browser has been closed. Restart the watcher "
                  "when you want it polling again.")
    else:
        detail = ("The bot has dropped its session for this account, so you can "
                  "sign in on your own laptop without a session conflict.\n\n"
                  "When you're done, sign the bot back in through noVNC and it "
                  "resumes polling on its own.")
    return _post({
        "embeds": [{
            "title": f"🔓 Handed over: {account}"[:250],
            "description": detail,
            "color": COLOR_TASK,
        }],
    }, webhook)


def notify_signed_in(account: str, webhook: str = "") -> bool:
    """Confirms a successful manual sign-in, so you know that window is done."""
    return _post({
        "embeds": [{
            "title": f"✅ Signed in: {account}"[:250],
            "description": "Now watching this account for available tasks.",
            "color": COLOR_TASK,
        }],
    }, webhook)
