"""Verify the hand-off flow: task -> notify -> release session -> pause -> resume."""
import os, sys, json, tempfile, pathlib

tmp = tempfile.mkdtemp()
os.environ.update({"STATE_DIR": tmp, "DISCORD_WEBHOOK_URL": "https://hook/X",
                   "EXTRACT_MODE": "broker"})
import pathlib as _pl
ROOT = _pl.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(tmp)
pathlib.Path("accounts.json").write_text(json.dumps(
    [{"name": "acct-1", "label": "Account 1"}]))

import config, accounts, notifier, watch

posts = []
class R:
    status_code = 204; text = ""
    def json(self): return {}
notifier.requests.post = lambda url, json=None, timeout=None: (posts.append(json), R())[1]

TASK = [{"id": "TASK-77", "title": "Segment Review", "detail": "d"}]

class FakeBrowser:
    def __init__(self):
        self.tasks = []
        self.rendered = True
        self.released = 0
        self.closed = 0
    def fetch_tasks(self): return self.tasks
    def release_session(self): self.released += 1
    def app_is_rendered(self): return self.rendered

def titles():
    out = []
    for p in posts:
        if p.get("content"): out.append(p["content"])
        for e in p.get("embeds", []):
            if e.get("title"): out.append(e["title"])
    return out

# ---------------- RELEASE_MODE=session ----------------
config.RELEASE_ON_TASK = True
config.RELEASE_MODE = "session"
acc = accounts.load()[0]
w = watch.AccountWatcher(acc)
fb = FakeBrowser(); w.browser = fb

print("=== cycle 1: empty queue ===")
w.poll()
assert posts == [], f"should be silent, got {titles()}"
assert not w.paused
print("  silent, still active. correct")

print("\n=== cycle 2: task appears ===")
fb.tasks = TASK
w.poll()
t = titles()
print("  discord:", t)
assert any("new task" in x for x in t), "no task notification"
assert any("Handed over" in x for x in t), "no hand-off notification"
assert fb.released == 1, f"session not released (released={fb.released})"
assert w.paused, "account not paused"
print("  task notified, session released once, account paused. correct")

print("\n=== cycle 3: paused, human still working (not signed in) ===")
posts.clear(); fb.rendered = False
w.check_resumed()
assert w.paused, "resumed too early!"
assert posts == [], f"should stay silent, got {titles()}"
assert fb.released == 1, "released again while paused!"
print("  stays paused, silent, no extra release. correct")

print("\n=== cycle 4: human signs bot back in ===")
fb.rendered = True
w.check_resumed()
assert not w.paused, "did not resume"
assert any("Signed in" in x for x in titles()), f"no resume notice: {titles()}"
print("  resumed and announced. correct")

print("\n=== cycle 5: same task still there -> already seen, no re-notify ===")
posts.clear()
w.poll()
assert posts == [], f"re-notified a known task: {titles()}"
print("  silent on already-seen task. correct")

print("\n=== cycle 6: a NEW task -> notify + hand off again ===")
posts.clear(); fb.tasks = [{"id": "TASK-88", "title": "Other", "detail": ""}]
w.poll()
assert fb.released == 2, f"expected 2nd release, got {fb.released}"
assert w.paused
print("  handed off again. correct")

# ---------------- RELEASE_MODE=browser ----------------
print("\n=== RELEASE_MODE=browser: closes and stops the account ===")
config.RELEASE_MODE = "browser"
w2 = watch.AccountWatcher(accounts.load()[0])
fb2 = FakeBrowser(); fb2.tasks = [{"id": "T-99", "title": "X", "detail": ""}]
w2.browser = fb2
w2.close = lambda: setattr(fb2, "closed", fb2.closed + 1)
posts.clear()
w2.poll()
assert fb2.closed == 1, "browser not closed"
assert w2.stopped and w2.paused, "account not stopped"
assert fb2.released == 0, "should not clear cookies when closing outright"
assert any("Handed over" in x for x in titles())
print("  browser closed, account stopped. correct")

print("\n=== REGRESSION: an already-signed-in account still announces itself ===")
# A session that survived a restart is just as much "now being watched" as one
# signed in by hand; a missing tick reads as a failed account.
class FakePage:
    def bring_to_front(self): pass
    def goto(self, *a, **k): pass
    def wait_for_timeout(self, *a, **k): pass

class AlreadyIn(FakeBrowser):
    def __init__(self):
        super().__init__()
        self.page = FakePage()
    def app_is_rendered(self): return True

config.RELEASE_ON_TASK = True
config.HEADLESS = True
w3 = watch.AccountWatcher(accounts.load()[0])
w3.browser = AlreadyIn()
posts.clear()
assert w3.bootstrap() is True, "bootstrap should succeed when already signed in"
t = titles()
print("  discord:", t)
assert any("Signed in" in x for x in t), f"no sign-in notice: {t}"
print("  announced without needing a manual sign-in. correct")

print("\nALL HAND-OFF ASSERTIONS PASSED")
