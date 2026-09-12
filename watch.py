"""Poll the Starshot broker page for several accounts and push new tasks to Discord.

Every account gets its own browser, and every browser stays open for the life of
the process: the SSO session cookie is session-scoped, so closing a browser
signs that account out with no way to restore it from disk.
"""

import logging
import sys
import time

import accounts as accounts_mod
import config
import notifier
import state
from scraper import (Browser, ExtractionError, SessionExpired,
                     slot_name, window_slot)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("watch")

FAILURES_BEFORE_ALERT = 3
STAGGER_SECONDS = 3  # small gap between accounts, so we don't fire them at once


class AccountWatcher:
    """One account: one browser, one seen-list, one Discord destination."""

    def __init__(self, account: accounts_mod.Account, index: int = 0, total: int = 1):
        self.account = account
        # Fixed screen position, so this account's window is always in the same
        # place and can never be confused with another account's.
        self.index, self.total = index, total
        self.slot = slot_name(index, total)
        self.browser: Browser | None = None
        self.failures = 0
        self.alerted = False
        self.signed_in = False
        # Set once a task is handed over to the human; cleared when they sign the
        # bot back in. A paused account is checked passively, never navigated.
        self.paused = False
        self.stopped = False

    # ---------- lifecycle ----------

    def start(self) -> None:
        self.browser = Browser(
            profile_dir=self.account.profile_dir,
            label=self.account.name,
            window=window_slot(self.index, self.total),
        ).__enter__()

    def close(self) -> None:
        if self.browser:
            try:
                self.browser.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - teardown must not mask real errors
                pass
            self.browser = None

    def restart(self) -> None:
        log.warning("[%s] restarting browser", self.account.label)
        self.close()
        self.start()
        self.signed_in = False

    # ---------- sign-in ----------

    def bootstrap(self) -> bool:
        """Wait for a human to sign this account in. Called one account at a time."""
        page = self.browser.page
        page.bring_to_front()
        page.goto(config.TARGET_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        if self.browser.app_is_rendered():
            # Still announce it. An account whose session survived a restart is
            # just as much "this account is now being watched" as one you had to
            # sign in by hand, and a missing tick looks like a failure.
            log.info("[%s] already signed in", self.account.label)
            notifier.notify_signed_in(self.account.label, self.account.webhook)
            self.signed_in = True
            return True

        if config.HEADLESS:
            msg = (
                "Cannot sign in: no visible browser. The SSO session cookie "
                "cannot be restored from disk, so a human must sign in. Run with "
                "HEADLESS=false and connect over noVNC."
            )
            log.error("[%s] %s", self.account.label, msg)
            notifier.notify_alert("Sign-in required", msg,
                                  self.account.label, self.account.webhook)
            return False

        banner = f"  >>> Sign in now for: {self.account.label}  <<<"
        where = f"  >>> Window: {self.slot} of the screen  <<<"
        width = max(len(banner), len(where))
        print("\n" + "=" * width); print(banner); print(where); print("=" * width)
        print(f"  (2FA will go to your phone. Waiting up to "
              f"{config.LOGIN_TIMEOUT // 60} minutes.)\n")
        log.info("[%s] waiting for sign-in - %s window",
                 self.account.label, self.slot)

        if self.browser.wait_until_logged_in(config.LOGIN_TIMEOUT):
            log.info("[%s] signed in", self.account.label)
            notifier.notify_signed_in(self.account.label, self.account.webhook)
            self.signed_in = True
            return True

        log.error("[%s] timed out waiting for sign-in", self.account.label)
        return False

    def hand_off(self) -> None:
        """A task appeared - get out of the way so it can be worked elsewhere."""
        label = self.account.label
        if config.RELEASE_MODE == "browser":
            log.info("[%s] task found; closing browser", label)
            self.close()
            self.stopped = True
        else:
            log.info("[%s] task found; releasing session", label)
            try:
                self.browser.release_session()
            except Exception:  # noqa: BLE001 - hand-off must not kill the loop
                log.exception("[%s] release failed", label)
        self.paused = True
        self.signed_in = False
        notifier.notify_released(label, config.RELEASE_MODE, self.account.webhook)

    def check_resumed(self) -> bool:
        """Has the human signed the bot back in? Passive: never navigates, so it
        cannot disturb a sign-in in progress in that window."""
        if self.stopped or not self.browser:
            return False
        try:
            if self.browser.app_is_rendered():
                log.info("[%s] signed back in; resuming polling",
                         self.account.label)
                notifier.notify_signed_in(self.account.label, self.account.webhook)
                self.paused = False
                self.signed_in = True
                self.failures, self.alerted = 0, False
                return True
        except Exception:  # noqa: BLE001 - a dead browser must not kill the loop
            log.debug("[%s] resume check failed", self.account.label)
        return False

    # ---------- polling ----------

    def poll(self) -> None:
        """One cycle for this account. Never raises: one bad account must not
        stop the others."""
        label = self.account.label
        try:
            tasks = self.browser.fetch_tasks()

            data = state.load(self.account.seen_file)
            fresh = state.select_new(data, tasks)

            if fresh:
                log.info("[%s] %d new task(s): %s", label, len(fresh),
                         ", ".join(t["title"][:40] for t in fresh))
                sent = notifier.notify_tasks(
                    fresh, len(tasks), label, self.account.webhook
                )
                if sent:
                    # Only commit once Discord accepted it, so a webhook outage
                    # cannot silently swallow a task.
                    state.save(data, self.account.seen_file)
                    if config.RELEASE_ON_TASK:
                        self.failures, self.alerted = 0, False
                        self.hand_off()
                        return
                else:
                    log.error("[%s] Discord delivery failed; will retry", label)
            else:
                log.info("[%s] no new tasks (%d open)", label, len(tasks))
                state.save(data, self.account.seen_file)

            if self.alerted:
                notifier.notify_alert("Recovered", "Polling normally again.",
                                      label, self.account.webhook)
            self.failures, self.alerted, self.signed_in = 0, False, True

        except SessionExpired as exc:
            # Recoverable without a restart: sign back in via noVNC in this same
            # window and the next poll picks it up.
            self.failures += 1
            self.signed_in = False
            log.error("[%s] %s", label, exc)
            if not self.alerted:
                notifier.notify_alert(
                    "Session expired",
                    "Sign in again through noVNC in this account's browser "
                    "window. Polling resumes automatically once you do.",
                    label, self.account.webhook,
                )
                self.alerted = True

        except ExtractionError as exc:
            self.failures += 1
            log.error("[%s] extraction failed: %s", label, exc)
            if self.failures >= FAILURES_BEFORE_ALERT and not self.alerted:
                notifier.notify_alert(
                    "Cannot read the task list",
                    f"{exc}\n\nThe layout may have changed. Re-run discover.py "
                    "and update TASK_SELECTOR.",
                    label, self.account.webhook,
                )
                self.alerted = True

        except Exception as exc:  # noqa: BLE001 - likely a dead browser
            self.failures += 1
            log.exception("[%s] unexpected error: %s", label, exc)
            if self.failures >= FAILURES_BEFORE_ALERT and not self.alerted:
                notifier.notify_alert(
                    "Watcher failing",
                    f"{self.failures} consecutive errors.\nLatest: "
                    f"{type(exc).__name__}: {exc}",
                    label, self.account.webhook,
                )
                self.alerted = True
            if self.failures % 5 == 0:
                try:
                    self.restart()
                except Exception:  # noqa: BLE001
                    log.exception("[%s] browser restart failed", label)


def main() -> int:
    try:
        config.validate()
        accs = accounts_mod.load()
    except (config.ConfigError, accounts_mod.AccountsError) as exc:
        log.error("%s", exc)
        return 2

    config.ensure_state_dir()
    log.info("Watching %d account(s) every %ds (mode=%s)",
             len(accs), config.POLL_INTERVAL, config.EXTRACT_MODE)

    watchers = [AccountWatcher(a, i, len(accs)) for i, a in enumerate(accs)]
    for w in watchers:
        log.info("  %s -> %s window", w.account.label, w.slot)

    try:
        # Sequential on purpose: each account needs its own 2FA approval, and
        # several browser windows all demanding attention at once is unusable.
        # Each browser is launched at its turn, so the window that just appeared
        # is always the one asking for a sign-in.
        signed_in = 0
        for w in watchers:
            w.start()
            if w.bootstrap():
                signed_in += 1
                # Poll straight away rather than waiting for the loop. A task may
                # already be sitting there, and with several sequential 2FA
                # sign-ins the first poll could otherwise be many minutes off.
                w.poll()

        if signed_in == 0:
            log.error("No accounts signed in; nothing to watch")
            return 3
        log.info("%d/%d account(s) signed in; entering poll loop",
                 signed_in, len(watchers))

        while True:
            started = time.monotonic()
            active = 0
            for w in watchers:
                if w.stopped:
                    continue
                if w.paused:
                    w.check_resumed()   # waiting on the human; do not navigate
                    continue
                active += 1
                w.poll()
                time.sleep(STAGGER_SECONDS)

            if all(w.stopped for w in watchers):
                log.info("Every account has been handed over; nothing left to watch")
                return 0

            worst = max((w.failures for w in watchers), default=0)
            delay = config.POLL_INTERVAL
            if worst:
                delay = min(config.POLL_INTERVAL * (2 ** min(worst, 4)), 1800)

            elapsed = time.monotonic() - started
            time.sleep(max(5.0, delay - elapsed))

    except KeyboardInterrupt:
        log.info("Stopped by user")
        return 0
    finally:
        for w in watchers:
            w.close()


if __name__ == "__main__":
    sys.exit(main())
