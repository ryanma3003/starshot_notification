"""Window tiling and lazy browser launch.

Five identical Chromium windows are impossible to tell apart. Signing an account
into the wrong window would store its session under another account's profile and
mislabel every notification after that, so these two behaviours matter:

  * each account has a distinct, stable, on-screen position
  * a browser is launched at its turn, so the window that just appeared is the
    one asking to be signed in
"""
import json
import os
import pathlib as _pl
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update({"STATE_DIR": tmp, "DISCORD_WEBHOOK_URL": "https://hook/X",
                   "EXTRACT_MODE": "broker", "SCREEN_GEOMETRY": "1920x1080x24"})
ROOT = _pl.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scraper  # noqa: E402
from scraper import slot_name, window_slot  # noqa: E402

print("=== positions are distinct and inside the screen ===")
for total in range(1, 7):
    slots = [window_slot(i, total, "1920x1080x24") for i in range(total)]
    assert len(set(slots)) == total, f"{total} accounts: positions collide -> {slots}"
    for x, y, w, h in slots:
        assert x >= 0 and y >= 0, f"off-screen: {(x, y)}"
        assert x + w <= 1920 and y + h <= 1080, f"overflows screen: {(x, y, w, h)}"
    names = [slot_name(i, total) for i in range(total)]
    assert len(set(names)) == total, f"{total} accounts: duplicate names -> {names}"
    print(f"  {total}: {names}")

print("\n=== windows do not overlap ===")
def overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

slots = [window_slot(i, 5, "1920x1080x24") for i in range(5)]
for i in range(len(slots)):
    for j in range(i + 1, len(slots)):
        assert not overlaps(slots[i], slots[j]), f"windows {i} and {j} overlap"
print("  5 windows tile without overlapping")

print("\n=== layout adapts to a different screen size ===")
big = window_slot(0, 5, "2560x1440x24")
assert big[2] > slots[0][2], "did not scale with a larger screen"
print(f"  1920x1080 -> {slots[0][2]}x{slots[0][3]} per window")
print(f"  2560x1440 -> {big[2]}x{big[3]} per window")

print("\n=== the window args actually reach Chromium ===")
src = (ROOT / "scraper.py").read_text()
assert "--window-position=" in src and "--window-size=" in src
assert "no_viewport" in src, "window size would fight the viewport setting"
print("  --window-position / --window-size passed, viewport yielded to the window")

# ---------------------------------------------------------------- lazy launch
print("\n=== browsers launch one at a time, at their turn ===")
os.chdir(tmp)
_pl.Path("accounts.json").write_text(json.dumps([
    {"name": f"acct-{i}", "label": f"A{i}"} for i in range(1, 6)]))

import accounts  # noqa: E402
import config  # noqa: E402
import watch  # noqa: E402

events = []
real_init = watch.AccountWatcher.__init__

class FakeWatcher(watch.AccountWatcher):
    def start(self):
        events.append(("start", self.account.label))
        self.browser = object()
    def bootstrap(self):
        events.append(("bootstrap", self.account.label))
        return True
    def poll(self):
        events.append(("poll", self.account.label))
    def close(self):
        pass

watch.AccountWatcher = FakeWatcher
# Break out of the endless poll loop once bootstrap has finished.
watch.time.sleep = lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt())

rc = watch.main()
watch.AccountWatcher.__init__ = real_init

print("  event order:")
for kind, who in events:
    print(f"    {kind:<10} {who}")

labels = [f"A{i}" for i in range(1, 6)]
expected = []
for lb in labels:
    expected += [("start", lb), ("bootstrap", lb), ("poll", lb)]
assert events[:len(expected)] == expected, (
    "browsers were not launched lazily at their turn:\n"
    f"  expected {expected[:6]}...\n  got      {events[:6]}...")
print("\n  each account: start -> bootstrap -> poll, before the next one begins")

starts_before_second_bootstrap = [
    i for i, e in enumerate(events) if e == ("start", "A2")]
first_bootstrap = events.index(("bootstrap", "A1"))
assert starts_before_second_bootstrap[0] > first_bootstrap, (
    "A2's browser opened before A1 had been signed in")
print("  no later window opens while an earlier one awaits sign-in")

print("\nWINDOW-LAYOUT ASSERTIONS PASSED")
