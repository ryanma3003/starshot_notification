"""Reproduce the multi-account crash and prove the shared-runtime fix.

This is the bug the single-account test could not catch: the second concurrent
browser used to die with "Sync API inside the asyncio loop".
"""
import os, sys, tempfile, pathlib

os.environ.update({"STATE_DIR": tempfile.mkdtemp(), "HEADLESS": "true"})
import pathlib as _pl
ROOT = _pl.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scraper
from scraper import Browser

root = pathlib.Path(tempfile.mkdtemp())
N = 3

print(f"=== opening {N} browsers at once (the case that crashed) ===")
browsers = []
try:
    for i in range(1, N + 1):
        b = Browser(profile_dir=root / f"acct-{i}", label=f"acct-{i}").__enter__()
        browsers.append(b)
        print(f"  acct-{i}: opened")

    assert scraper._PW_REFS == N, f"refcount {scraper._PW_REFS}, expected {N}"

    print(f"\n=== all {N} usable simultaneously ===")
    for i, b in enumerate(browsers, 1):
        b.page.set_content(f"<div id='root'><p>browser {i}</p></div>")
        text = b.page.locator("#root").inner_text().strip()
        assert text == f"browser {i}", f"acct-{i} got {text!r}"
        print(f"  acct-{i}: renders independently -> {text!r}")

    print("\n=== isolated profiles ===")
    dirs = {str(b.profile_dir) for b in browsers}
    assert len(dirs) == N, "profile dirs collided"
    print(f"  {N} distinct profile dirs on disk")

finally:
    for i, b in enumerate(browsers, 1):
        b.__exit__(None, None, None)
        print(f"  acct-{i}: closed (refs now {scraper._PW_REFS})")

assert scraper._PW is None, "shared runtime leaked after last close"
assert scraper._PW_REFS == 0, f"refcount leaked: {scraper._PW_REFS}"
print("\n=== runtime released cleanly after the last browser ===")

print("\n=== re-acquire after full release (restart path) ===")
b = Browser(profile_dir=root / "acct-1", label="acct-1").__enter__()
b.page.set_content("<div id='root'>ok</div>")
assert b.page.locator("#root").inner_text().strip() == "ok"
b.__exit__(None, None, None)
assert scraper._PW is None
print("  reopened and closed cleanly")

print("\nMULTI-BROWSER ASSERTIONS PASSED")
