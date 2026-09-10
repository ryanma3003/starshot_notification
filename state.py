"""Tracks which tasks we've already announced, so restarts don't re-notify."""

import json
import logging
import time

import config

log = logging.getLogger("state")

# Forget a task id this long after we last saw it in the queue. Keeps the file
# small, and lets a genuinely recycled id notify again.
TTL_SECONDS = 7 * 24 * 3600


def load(path=None) -> dict:
    path = path or config.SEEN_FILE
    if not path.exists():
        return {"seen": {}, "last_count": 0}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s (%s); starting fresh", path, exc)
        return {"seen": {}, "last_count": 0}

    data.setdefault("seen", {})
    data.setdefault("last_count", 0)
    return data


def save(data: dict, path=None) -> None:
    path = path or config.SEEN_FILE
    config.ensure_state_dir()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)  # atomic, so a crash can't truncate the file


def select_new(data: dict, tasks: list[dict]) -> list[dict]:
    """Return the subset of `tasks` we have not announced, and update the state."""
    now = time.time()
    seen = data["seen"]
    previous_count = data.get("last_count", 0)

    fresh = [t for t in tasks if t["id"] not in seen]

    # Optional: the queue emptied and refilled. Announce everything again even if
    # the ids look familiar, because some backends recycle ids.
    if config.NOTIFY_ON_REFILL and previous_count == 0 and tasks and not fresh:
        log.info("Queue refilled (0 -> %d) with previously-seen ids", len(tasks))
        fresh = list(tasks)

    for task in tasks:
        seen[task["id"]] = now

    for task_id, last_seen in list(seen.items()):
        if now - last_seen > TTL_SECONDS:
            del seen[task_id]

    data["last_count"] = len(tasks)
    return fresh
