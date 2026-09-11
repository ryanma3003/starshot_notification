"""Drives a headless Chromium: logs in, then reads the available-task list."""

import hashlib
import json
import logging
import math
import os
import pathlib
import re
import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

import config

log = logging.getLogger("scraper")


class SessionExpired(RuntimeError):
    """The saved cookies are no longer valid and a human must sign in again."""


class ExtractionError(RuntimeError):
    """The page loaded but did not look like the app we configured for."""


def _task_id(raw_id: str | None, title: str) -> str:
    """Prefer the site's own id; otherwise hash the text so it stays stable."""
    if raw_id:
        return str(raw_id).strip()
    normalized = re.sub(r"\s+", " ", title).strip().lower()
    return "h:" + hashlib.sha1(normalized.encode()).hexdigest()[:16]


def _dig(payload, dotted_path: str):
    """Walk `data.tasks.items` style paths; return None if any hop is missing."""
    if not dotted_path:
        return payload
    current = payload
    for key in dotted_path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            return None
    return current


# Playwright's sync API drives one driver process per thread, so calling
# sync_playwright().start() once per account fails on the second account with
# "Sync API inside the asyncio loop". One runtime, shared by every browser.
_PW = None
_PW_REFS = 0


def _acquire_playwright():
    global _PW, _PW_REFS
    if _PW is None:
        _PW = sync_playwright().start()
    _PW_REFS += 1
    return _PW


def _release_playwright() -> None:
    """Stop the shared runtime only once the last browser has let go of it."""
    global _PW, _PW_REFS
    _PW_REFS -= 1
    if _PW_REFS <= 0:
        if _PW is not None:
            try:
                _PW.stop()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
        _PW, _PW_REFS = None, 0


def _screen_size(geometry: str | None = None) -> tuple[int, int]:
    """Read the virtual screen size the container was started with."""
    geom = geometry or os.getenv("SCREEN_GEOMETRY", "1920x1080x24")
    match = re.match(r"(\d+)x(\d+)", geom)
    return (int(match.group(1)), int(match.group(2))) if match else (1920, 1080)


def _grid(total: int) -> tuple[int, int]:
    cols = 1 if total <= 1 else (2 if total <= 4 else 3)
    return cols, math.ceil(total / cols)


def window_slot(index: int, total: int, geometry: str | None = None):
    """Fixed screen position for an account's window.

    Five identical Chromium windows on one screen are impossible to tell apart,
    and signing an account into the wrong window would put its session in another
    account's profile and mislabel every notification from then on. Tiling gives
    each account a position that never changes.
    """
    screen_w, screen_h = _screen_size(geometry)
    cols, rows = _grid(total)
    width, height = screen_w // cols, screen_h // rows
    col, row = index % cols, index // cols
    return col * width, row * height, width, height


def slot_name(index: int, total: int) -> str:
    """Human description of that position, for the log and the sign-in banner."""
    cols, rows = _grid(total)
    col, row = index % cols, index // cols
    # Column names depend on how many columns there are: with two, the second
    # one is "right", not "middle".
    names = {2: ["left", "right"], 3: ["left", "middle", "right"]}
    horizontal = names.get(cols, [str(col + 1)])[col] if cols > 1 else ""
    vertical = ["top", "bottom"][row] if rows > 1 else ""
    if horizontal and vertical:
        return f"{vertical}-{horizontal}"
    return horizontal or vertical or "fullscreen"


class Browser:
    """Owns the Playwright lifecycle and a persistent, reusable login session."""

    def __init__(self, profile_dir=None, label: str = "default", window=None):
        # Each account needs its own profile directory: one browser per account,
        # all held open at once, because closing any of them signs that account out.
        self.profile_dir = pathlib.Path(profile_dir) if profile_dir else config.PROFILE_DIR
        self.label = label
        # (x, y, width, height) for this account's window, or None to let the
        # window manager place it.
        self.window = window
        self._pw = None
        self._context = None
        self.page = None
        self._api_payloads: list = []

    def __enter__(self):
        self._pw = _acquire_playwright()

        # A persistent profile, not a serialized storage_state. Apple SSO keeps
        # session state in IndexedDB and partitioned cookies, which storage_state
        # silently drops - restoring it left us back at the sign-in page.
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        existed = any(self.profile_dir.iterdir())
        log.info(
            "[%s] %s browser profile at %s",
            self.label, "Reusing" if existed else "Creating", self.profile_dir,
        )
        args = ["--no-sandbox", "--disable-dev-shm-usage"]  # required in Docker
        kwargs = {"viewport": {"width": 1440, "height": 900}}
        if self.window:
            x, y, width, height = self.window
            args += [f"--window-position={x},{y}", f"--window-size={width},{height}"]
            # Let the OS window drive the page size, or the viewport and the
            # window disagree and the page renders at the wrong size.
            kwargs = {"no_viewport": True}

        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=config.HEADLESS,
            args=args,
            **kwargs,
        )
        self._context.set_default_timeout(config.NAV_TIMEOUT_MS)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self.page.on("response", self._capture_response)
        return self

    def __exit__(self, *exc_info):
        # Closing the context is what flushes the profile to disk. Skipping it
        # loses the session we just established.
        try:
            if self._context:
                self._context.close()
        except PlaywrightError:
            pass
        if self._pw:
            self._pw = None
            _release_playwright()
        return False

    # ---------- network capture (API mode + discovery) ----------

    def _capture_response(self, response) -> None:
        ctype = (response.headers or {}).get("content-type", "")
        if "json" not in ctype.lower():
            return
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - body may be gone or not valid JSON
            return
        self._api_payloads.append({"url": response.url, "body": body})

    def reset_capture(self) -> None:
        self._api_payloads = []

    @property
    def captured(self) -> list:
        return self._api_payloads

    # ---------- session ----------

    def save_session(self) -> None:
        """Debug aid only - auth cannot actually be restored from disk."""
        config.ensure_state_dir()
        try:
            self._context.storage_state(
                path=str(config.STATE_DIR / f"session-{self.label}.json"))
        except PlaywrightError as exc:
            log.debug("[%s] could not write session dump: %s", self.label, exc)

    def release_session(self) -> None:
        """Drop every trace of this account's session from the browser.

        Used when a task appears and the human wants to work it on their own
        machine: the bot must not keep holding a session for the same account.
        Clears the app's stored token first (it lives in localStorage, so cookie
        clearing alone would leave it behind), then the cookies, then parks the
        window at the login page ready for the next sign-in.
        """
        try:
            self.page.evaluate(
                "() => { try { localStorage.clear(); sessionStorage.clear(); } "
                "catch (e) {} }"
            )
        except PlaywrightError as exc:
            log.debug("[%s] could not clear web storage: %s", self.label, exc)

        try:
            self._context.clear_cookies()
        except PlaywrightError as exc:
            log.warning("[%s] could not clear cookies: %s", self.label, exc)

        try:
            self.page.goto(config.TARGET_URL, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
        except PlaywrightError as exc:
            log.debug("[%s] could not park at login page: %s", self.label, exc)

        log.info("[%s] session released; browser holds no session now", self.label)

    def is_logged_out(self) -> bool:
        """Two tells: we got bounced to the identity provider, or a password prompt."""
        try:
            if urlparse(self.page.url).netloc != urlparse(config.TARGET_URL).netloc:
                return True
            return self.page.locator('input[type="password"]:visible').count() > 0
        except PlaywrightError:
            return False

    def open_target(self) -> None:
        """Navigate to the task page, logging in first if the session has lapsed."""
        self.reset_capture()
        self.page.goto(config.TARGET_URL, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=config.NAV_TIMEOUT_MS)
        except PlaywrightTimeout:
            log.debug("networkidle not reached; the app may poll continuously")

        if self.is_logged_out():
            # Only a human can fix this: AppleConnect needs 2FA from your phone.
            raise SessionExpired(
                f"Not signed in (landed on {urlparse(self.page.url).netloc}). "
                "Sign in again through noVNC in this account's browser window."
            )

        self.page.wait_for_timeout(1500)

    # ---------- extraction ----------

    def fetch_tasks(self) -> list[dict]:
        self.open_target()
        if config.EXTRACT_MODE == "api":
            return self._tasks_from_api()
        if config.EXTRACT_MODE == "broker":
            return self._tasks_from_broker()
        return self._tasks_from_dom()

    def _tasks_from_broker(self) -> list[dict]:
        """Starshot serves one task at a time, so availability is binary.

        Keyed off stable hooks - the modal's text, the submit button's id, and
        the h1 aria-label - never the styled-components class hashes, which are
        regenerated on every frontend build.
        """
        page = self.page

        try:
            page.wait_for_selector(config.APP_READY_SELECTOR, timeout=20000)
        except PlaywrightTimeout as exc:
            raise ExtractionError(
                f"APP_READY_SELECTOR {config.APP_READY_SELECTOR!r} never appeared - "
                "the page did not render. Not reporting an empty queue."
            ) from exc

        if config.CLICK_RETRY:
            # Requests a task. This can ASSIGN one and start its work timer.
            try:
                btn = page.get_by_role("button", name="Try Again")
                if btn.count() > 0:
                    log.warning("[%s] clicking 'Try Again' (CLICK_RETRY=true)",
                                self.label)
                    btn.first.click()
                    page.wait_for_timeout(4000)
            except PlaywrightError as exc:
                log.debug("[%s] retry click failed: %s", self.label, exc)

        body = page.locator("body").inner_text() or ""
        if config.NO_TASKS_TEXT.lower() in body.lower():
            log.info("[%s] broker reports no available tasks", self.label)
            return []

        # No "no tasks" message. Confirm a task is really loaded before claiming
        # one exists - an unrecognised error screen must not read as "task!".
        title, raw_id = "", None
        try:
            h1 = page.locator(config.TASK_TITLE_SELECTOR)
            if h1.count() > 0:
                aria = h1.first.get_attribute("aria-label") or ""
                match = re.search(r"\(([^)]*)\)\s*$", aria)
                if match and match.group(1).strip().lower() != "undefined":
                    raw_id = match.group(1).strip()
                title = (h1.first.inner_text() or "").strip()
        except PlaywrightError:
            pass

        has_submit = False
        try:
            has_submit = page.locator(config.SUBMIT_BUTTON_SELECTOR).count() > 0
        except PlaywrightError:
            pass

        if not raw_id and not has_submit:
            raise ExtractionError(
                "Neither the 'no available tasks' message nor a task was found. "
                "The page is in an unrecognised state; refusing to guess. "
                f"First 200 chars: {body[:200]!r}"
            )

        title = title or raw_id or "Task available"
        log.info("[%s] task available: %s", self.label, title)
        return [{
            "id": _task_id(raw_id, title),
            "title": title[:250],
            "detail": "A task is waiting in the broker. Open Starshot to claim it.",
        }]

    def _tasks_from_dom(self) -> list[dict]:
        page = self.page

        # Guard: if the app itself never rendered, an empty task list is a lie.
        try:
            page.wait_for_selector(config.APP_READY_SELECTOR, timeout=20000)
        except PlaywrightTimeout as exc:
            raise ExtractionError(
                f"APP_READY_SELECTOR {config.APP_READY_SELECTOR!r} never appeared - "
                "the page did not render. Not reporting an empty queue."
            ) from exc

        # Tasks genuinely may be absent, so a timeout here means "none", not an error.
        try:
            page.wait_for_selector(config.TASK_SELECTOR, timeout=10000)
        except PlaywrightTimeout:
            log.info("No elements matched TASK_SELECTOR - queue looks empty")
            return []

        rows = page.locator(config.TASK_SELECTOR)
        tasks = []
        for i in range(rows.count()):
            row = rows.nth(i)
            try:
                text = (row.inner_text() or "").strip()
            except PlaywrightError:
                continue
            if not text:
                continue

            raw_id = None
            if config.TASK_ID_ATTR:
                try:
                    raw_id = row.get_attribute(config.TASK_ID_ATTR)
                except PlaywrightError:
                    raw_id = None

            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            tasks.append({
                "id": _task_id(raw_id, text),
                "title": lines[0] if lines else text[:80],
                "detail": "\n".join(lines[1:6]),
            })

        log.info("Extracted %d task(s) from the DOM", len(tasks))
        return tasks

    def _tasks_from_api(self) -> list[dict]:
        matches = [p for p in self._api_payloads if config.TASK_API_PATTERN in p["url"]]
        if not matches:
            raise ExtractionError(
                f"No JSON response URL contained {config.TASK_API_PATTERN!r}. "
                "The endpoint may have changed - re-run discover.py."
            )

        payload = matches[-1]["body"]  # freshest wins
        items = _dig(payload, config.TASK_API_LIST_PATH)
        if items is None:
            raise ExtractionError(
                f"TASK_API_LIST_PATH {config.TASK_API_LIST_PATH!r} not found in the "
                f"response from {matches[-1]['url']}"
            )
        if not isinstance(items, list):
            raise ExtractionError(
                f"Expected a list at {config.TASK_API_LIST_PATH!r}, got "
                f"{type(items).__name__}"
            )

        tasks = []
        for item in items:
            if not isinstance(item, dict):
                tasks.append({"id": _task_id(None, str(item)), "title": str(item)[:120], "detail": ""})
                continue
            title = str(item.get(config.TASK_API_TITLE_FIELD) or "Task")
            tasks.append({
                "id": _task_id(item.get(config.TASK_API_ID_FIELD), title),
                "title": title[:250],
                "detail": json.dumps(
                    {k: v for k, v in item.items() if not isinstance(v, (dict, list))},
                    indent=1,
                )[:400],
            })

        log.info("Extracted %d task(s) from %s", len(tasks), matches[-1]["url"])
        return tasks


    def app_is_rendered(self) -> bool:
        """Signed in AND the SPA has painted - not merely mid-redirect."""
        if self.is_logged_out():
            return False
        try:
            return self.page.locator(config.APP_READY_SELECTOR).count() > 0
        except PlaywrightError:
            return False

    def wait_until_logged_in(self, timeout_s: int = 300) -> bool:
        """Poll until the human finishes signing in (password, 2FA, security key).

        Landing back on the app host is NOT enough: the OAuth redirect arrives
        with #code=... in the fragment before the app has exchanged it for
        tokens. Saving there captures a half-built session, so we wait for the
        app to actually render and hold that state.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.app_is_rendered():
                self.page.wait_for_timeout(3000)
                if self.app_is_rendered():  # stable, not a flicker mid-redirect
                    self.page.wait_for_timeout(4000)  # let token exchange settle
                    return True
            self.page.wait_for_timeout(2000)
        return False
