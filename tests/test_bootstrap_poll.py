"""A task waiting at sign-in time must notify immediately, not after every
other account has finished its 2FA."""
import os, sys, json, tempfile, pathlib

tmp = tempfile.mkdtemp()
os.environ.update({"STATE_DIR": tmp, "DISCORD_WEBHOOK_URL": "https://hook/X",
                   "EXTRACT_MODE": "broker", "TASK_SELECTOR": "div.x"})
import pathlib as _pl
ROOT = _pl.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(tmp)
pathlib.Path("accounts.json").write_text(json.dumps([
    {"name": "acct-1", "label": "A1"}, {"name": "acct-2", "label": "A2"},
    {"name": "acct-3", "label": "A3"}, {"name": "acct-4", "label": "A4"},
]))

import config, accounts, notifier, watch

events = []
class R:
    status_code = 204; text = ""
    def json(self): return {}
def fake_post(url, json=None, timeout=None):
    for e in json.get("embeds", []):
        if e.get("title"): events.append(("discord", e["title"]))
    if json.get("content"): events.append(("discord", json["content"]))
    return R()
notifier.requests.post = fake_post

class FakeBrowser:
    def __init__(self, tasks): self.tasks = tasks
    def fetch_tasks(self): return self.tasks
    def release_session(self): pass
    def app_is_rendered(self): return True

# acct-3 has a task waiting the instant it signs in.
QUEUES = {
    "acct-1": [], "acct-2": [],
    "acct-3": [{"id": "ADM-1", "title": "ADM Creation Model", "detail": ""}],
    "acct-4": [],
}

accs = accounts.load()
watchers = []
for a in accs:
    w = watch.AccountWatcher(a)
    w.browser = FakeBrowser(QUEUES[a.name])
    w.bootstrap = (lambda ww: (lambda: (events.append(("signin", ww.account.label)),
                                        True)[1]))(w)
    watchers.append(w)

# Mirrors main()'s bootstrap sequence.
for w in watchers:
    if w.bootstrap():
        w.poll()

print("event order:")
for kind, detail in events:
    print(f"  {kind:<8} {detail}")

order = [d for _, d in events]
task_idx = next(i for i, d in enumerate(order) if "ADM Creation Model" in d)
a4_idx = next(i for i, d in enumerate(order) if d == "A4")

assert task_idx < a4_idx, (
    "task notified only AFTER later accounts signed in — the gap is still there")
print(f"\ntask notified at step {task_idx}, before A4 signs in at step {a4_idx}")

handoff_idx = next(i for i, d in enumerate(order) if "Handed over" in d)
assert handoff_idx < a4_idx, "hand-off deferred past later sign-ins"
print(f"handed over at step {handoff_idx}, also before A4")

print("\nBOOTSTRAP-POLL ASSERTIONS PASSED")
