"""One-off reconnaissance: log in, then dump everything needed to configure watch.py.

Writes to ./discovery/:
  page.html          - the fully rendered DOM after login
  page.png           - full-page screenshot
  api/*.json         - every JSON response the app fetched
  candidates.txt     - guessed CSS selectors for repeated task-like elements

Run this once, look at the output, then set TASK_SELECTOR (or the TASK_API_*
values) in your .env.
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import accounts as accounts_mod
import config
from scraper import Browser, SessionExpired

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("discover")

OUT = Path("./discovery")

# Finds groups of sibling elements sharing a class - i.e. list rows or cards.
CANDIDATE_JS = """
() => {
  const groups = new Map();
  for (const el of document.querySelectorAll('body *')) {
    const parent = el.parentElement;
    if (!parent) continue;
    const cls = (el.className && typeof el.className === 'string')
      ? el.className.trim().split(/\\s+/).slice(0, 3).join('.')
      : '';
    if (!cls) continue;
    const key = el.tagName.toLowerCase() + '.' + cls;
    const entry = groups.get(key) || { count: 0, sample: '' };
    entry.count += 1;
    if (!entry.sample) entry.sample = (el.innerText || '').trim().slice(0, 160);
    groups.set(key, entry);
  }
  return [...groups.entries()]
    .filter(([, v]) => v.count >= 2 && v.count <= 200 && v.sample.length > 0)
    .sort((a, b) => b[1].count - a[1].count)
    .slice(0, 40)
    .map(([k, v]) => ({ selector: k, count: v.count, sample: v.sample }));
}
"""


def safe_name(url: str, index: int) -> str:
    path = urlparse(url).path or "root"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")[:60] or "root"
    return f"{index:03d}_{slug}.json"


def main() -> int:
    config.require_target_url()

    # Discovery always runs headed: a fresh browser is never signed in, because
    # the SSO session cookie cannot be restored from disk.
    config.HEADLESS = False

    OUT.mkdir(exist_ok=True)
    (OUT / "api").mkdir(exist_ok=True)

    # Discovery only needs one account - the task list looks the same for all.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", help="account name from accounts.json")
    args = parser.parse_args()

    try:
        accs = accounts_mod.load()
    except accounts_mod.AccountsError as exc:
        log.error("%s", exc)
        return 2

    if args.account:
        match = [a for a in accs if a.name == args.account]
        if not match:
            log.error("No account named %r. Known: %s",
                      args.account, ", ".join(a.name for a in accs))
            return 2
        account = match[0]
    else:
        account = accs[0]
        if len(accs) > 1:
            log.info("Using first account %r (override with --account)", account.name)

    with Browser(profile_dir=account.profile_dir, label=account.name) as browser:
        browser.page.goto(config.TARGET_URL, wait_until="domcontentloaded")
        browser.page.wait_for_timeout(2000)

        if not browser.app_is_rendered():
            print("\nSign in in the browser window that opened.")
            print(f"Waiting up to {config.LOGIN_TIMEOUT // 60} minutes. "
                  "Discovery starts automatically once the app loads.\n")
            if not browser.wait_until_logged_in(config.LOGIN_TIMEOUT):
                log.error("Timed out waiting for sign-in")
                return 1

        log.info("Signed in; capturing the page")
        browser.reset_capture()
        browser.page.goto(config.TARGET_URL, wait_until="domcontentloaded")
        try:
            browser.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

        # Give lazily-loaded task lists a chance to arrive.
        browser.page.wait_for_timeout(5000)

        html = browser.page.content()
        (OUT / "page.html").write_text(html)
        browser.page.screenshot(path=str(OUT / "page.png"), full_page=True)
        log.info("Saved page.html (%d bytes) and page.png", len(html))

        for i, payload in enumerate(browser.captured):
            (OUT / "api" / safe_name(payload["url"], i)).write_text(
                json.dumps(payload, indent=2)[:2_000_000]
            )
        log.info("Saved %d JSON response(s) to discovery/api/", len(browser.captured))

        candidates = browser.page.evaluate(CANDIDATE_JS)
        lines = ["Repeated elements - one of these is probably the task row.", ""]
        for c in candidates:
            lines.append(f"[{c['count']:>3}x]  {c['selector']}")
            lines.append(f"        sample: {c['sample']!r}")
            lines.append("")
        (OUT / "candidates.txt").write_text("\n".join(lines))

        print("\n=== Candidate task selectors ===")
        for c in candidates[:15]:
            print(f"  [{c['count']:>3}x] {c['selector']}\n         {c['sample'][:100]!r}")

        print("\n=== JSON endpoints the app called ===")
        for payload in browser.captured:
            body = payload["body"]
            shape = f"list[{len(body)}]" if isinstance(body, list) else (
                "keys: " + ", ".join(list(body)[:8]) if isinstance(body, dict) else type(body).__name__
            )
            print(f"  {payload['url'][:110]}\n         {shape}")

    print("\nFull output in ./discovery/ - open page.png to see what the bot sees.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
