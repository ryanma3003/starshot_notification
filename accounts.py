"""Account definitions.

Each account is just a label plus where its browser profile and seen-task list
live. No credentials are stored anywhere: you sign in to each account by hand,
because every one of them needs 2FA from your phone.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import config

log = logging.getLogger("accounts")

ACCOUNTS_FILE = Path("accounts.json")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class AccountsError(RuntimeError):
    """The accounts file is missing or malformed."""


@dataclass(frozen=True)
class Account:
    name: str            # filesystem-safe id, used for profile + state paths
    label: str           # what you see in Discord
    webhook: str         # per-account override, or the global webhook

    @property
    def profile_dir(self) -> Path:
        return config.STATE_DIR / "profiles" / self.name

    @property
    def seen_file(self) -> Path:
        return config.STATE_DIR / f"seen-{self.name}.json"


def load() -> list[Account]:
    """Read accounts.json, or fall back to a single account using global config."""
    if not ACCOUNTS_FILE.exists():
        log.info("No %s; running with a single account", ACCOUNTS_FILE)
        return [Account(name="default", label="Starshot",
                        webhook=config.DISCORD_WEBHOOK_URL)]

    try:
        raw = json.loads(ACCOUNTS_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise AccountsError(f"Could not read {ACCOUNTS_FILE}: {exc}") from exc

    if not isinstance(raw, list) or not raw:
        raise AccountsError(f"{ACCOUNTS_FILE} must be a non-empty JSON array")

    accounts: list[Account] = []
    seen_names: set[str] = set()

    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise AccountsError(f"{ACCOUNTS_FILE}[{i}] is not an object")

        name = str(entry.get("name", "")).strip()
        if not SAFE_NAME.match(name):
            raise AccountsError(
                f"{ACCOUNTS_FILE}[{i}]: name {name!r} must be 1-64 chars of "
                "letters, digits, dot, dash or underscore (it becomes a directory)"
            )
        if name in seen_names:
            raise AccountsError(f"{ACCOUNTS_FILE}: duplicate account name {name!r}")
        seen_names.add(name)

        webhook = str(entry.get("webhook") or config.DISCORD_WEBHOOK_URL).strip()
        if not webhook:
            raise AccountsError(
                f"{ACCOUNTS_FILE}[{i}] ({name}): no webhook, and DISCORD_WEBHOOK_URL "
                "is not set either"
            )

        accounts.append(Account(
            name=name,
            label=str(entry.get("label") or name).strip(),
            webhook=webhook,
        ))

    log.info("Loaded %d account(s): %s",
             len(accounts), ", ".join(a.label for a in accounts))
    return accounts
