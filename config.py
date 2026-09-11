"""Configuration for the Starshot task notifier, read from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# No default on purpose: the target is deployment configuration, not something
# this repository should name. A missing value fails loudly rather than quietly
# requesting the wrong host.
TARGET_URL = _str("STARSHOT_URL")

DISCORD_WEBHOOK_URL = _str("DISCORD_WEBHOOK_URL")
DISCORD_MENTION = _str("DISCORD_MENTION")

POLL_INTERVAL = _int("POLL_INTERVAL_SECONDS", 300)
NAV_TIMEOUT_MS = _int("NAV_TIMEOUT_MS", 45000)
# How long to wait for a human to finish signing in. Generous by default:
# this step waits on a person, not on the network.
LOGIN_TIMEOUT = _int("LOGIN_TIMEOUT_SECONDS", 900)
HEADLESS = _bool("HEADLESS", True)
NOTIFY_ON_REFILL = _bool("NOTIFY_ON_REFILL", False)


EXTRACT_MODE = _str("EXTRACT_MODE", "broker").lower()

TASK_SELECTOR = _str("TASK_SELECTOR")
TASK_ID_ATTR = _str("TASK_ID_ATTR")
APP_READY_SELECTOR = _str("APP_READY_SELECTOR", "#root > *")

TASK_API_PATTERN = _str("TASK_API_PATTERN")
TASK_API_LIST_PATH = _str("TASK_API_LIST_PATH")
TASK_API_ID_FIELD = _str("TASK_API_ID_FIELD", "id")
TASK_API_TITLE_FIELD = _str("TASK_API_TITLE_FIELD", "name")

# --- broker mode -------------------------------------------------------------
# Starshot hands out ONE task at a time; it is not a list. Availability is
# binary, so we key off stable hooks rather than the styled-components class
# hashes (sc-gKkgUA etc), which change on every frontend rebuild.
NO_TASKS_TEXT = _str("NO_TASKS_TEXT", "no available tasks")
TASK_TITLE_SELECTOR = _str(
    "TASK_TITLE_SELECTOR", 'h1[aria-label^="Show info about this task"]')
SUBMIT_BUTTON_SELECTOR = _str("SUBMIT_BUTTON_SELECTOR", "#starshot_submit_button")
# Clicking "Try Again" REQUESTS a task, which may assign one to your account and
# start its work timer. Off by default: see the README warning.
CLICK_RETRY = _bool("CLICK_RETRY", False)

# --- hand-off ----------------------------------------------------------------
# When a task appears, drop this account's session so you can sign in on your own
# laptop without fighting the bot for the same session.
#   session = clear cookies + stored token, leave the window at the login page.
#             Sign the bot back in through noVNC and polling resumes by itself.
#   browser = close this account's browser entirely. The account then stops until
#             the watcher is restarted.
RELEASE_ON_TASK = _bool("RELEASE_ON_TASK", True)
RELEASE_MODE = _str("RELEASE_MODE", "session").lower()

STATE_DIR = Path(_str("STATE_DIR", "./state")).expanduser()
SESSION_FILE = STATE_DIR / "session.json"
# A whole browser profile, not just cookies. The SSO provider keeps state in
# places storage_state does not capture (IndexedDB, partitioned cookies), so we
# persist the entire profile directory instead.
PROFILE_DIR = STATE_DIR / "profile"
SEEN_FILE = STATE_DIR / "seen.json"


class ConfigError(RuntimeError):
    """Raised when the configuration is incomplete enough that the watcher cannot run."""


def validate() -> None:
    """Fail fast with an actionable message rather than dying mid-poll."""
    missing = []
    if not TARGET_URL:
        missing.append("STARSHOT_URL")
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")
    if missing:
        raise ConfigError(
            "Missing required settings: " + ", ".join(missing)
            + "\nCopy .env.example to .env and fill them in."
        )

    if EXTRACT_MODE == "dom" and not TASK_SELECTOR:
        raise ConfigError(
            "EXTRACT_MODE=dom needs TASK_SELECTOR. Run `python discover.py` first "
            "and pick the selector that matches one task row."
        )
    if EXTRACT_MODE == "api" and not TASK_API_PATTERN:
        raise ConfigError(
            "EXTRACT_MODE=api needs TASK_API_PATTERN. Run `python discover.py` first "
            "to see which endpoint returns the task list."
        )
    if RELEASE_MODE not in ("session", "browser"):
        raise ConfigError(
            f"RELEASE_MODE must be 'session' or 'browser', got {RELEASE_MODE!r}")

    if EXTRACT_MODE not in ("broker", "dom", "api"):
        raise ConfigError(
            f"EXTRACT_MODE must be 'broker', 'dom' or 'api', got {EXTRACT_MODE!r}")


def require_target_url() -> None:
    """For entry points that do not run the full validate()."""
    if not TARGET_URL:
        raise ConfigError(
            "STARSHOT_URL is not set. Put the broker page URL in your .env - "
            "there is no built-in default."
        )


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
