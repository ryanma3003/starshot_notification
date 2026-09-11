import os, sys, json, tempfile, pathlib
tmp = tempfile.mkdtemp()
os.environ.update({"STATE_DIR": tmp, "DISCORD_WEBHOOK_URL": "https://hook/GLOBAL",
                   "EXTRACT_MODE": "dom", "TASK_SELECTOR": "div.t"})
import pathlib as _pl
ROOT = _pl.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(tmp)

pathlib.Path("accounts.json").write_text(json.dumps([
    {"name": "acct-1", "label": "Main"},
    {"name": "acct-2", "label": "Second"},
    {"name": "acct-3", "label": "Third", "webhook": "https://hook/THIRD"},
]))

import config, accounts, notifier, watch

# Hand-off is covered by t_handoff.py; this file isolates routing + dedup, so
# accounts must keep polling rather than pausing on the first task.
config.RELEASE_ON_TASK = False

accs = accounts.load()
print("loaded:", [(a.name, a.label, a.webhook.rsplit('/',1)[-1]) for a in accs])
assert accs[2].webhook.endswith("THIRD"), "per-account webhook override failed"
assert accs[0].webhook.endswith("GLOBAL"), "global webhook fallback failed"
assert len({a.profile_dir for a in accs}) == 3, "profile dirs collide!"
assert len({a.seen_file for a in accs}) == 3, "seen files collide!"
print("profile dirs distinct:", [str(a.profile_dir).split('profiles/')[-1] for a in accs])

# --- capture Discord traffic instead of sending it ---
posts = []
class R:
    status_code = 204
    text = ""
    def json(self): return {}
notifier.requests.post = lambda url, json=None, timeout=None: (posts.append((url, json)), R())[1]

class FakeBrowser:
    def __init__(self, tasks): self.tasks = tasks
    def fetch_tasks(self): return self.tasks

# each account sees a different queue
queues = {
    "acct-1": [{"id": "a1", "title": "Alpha", "detail": ""}],
    "acct-2": [{"id": "b1", "title": "Beta", "detail": ""}],
    "acct-3": [{"id": "a1", "title": "Alpha", "detail": ""}],  # same id as acct-1
}
ws = [watch.AccountWatcher(a) for a in accs]
for w in ws:
    w.browser = FakeBrowser(queues[w.account.name])

print("\n--- cycle 1 ---")
for w in ws: w.poll()
print("discord posts:", len(posts))
for url, body in posts:
    print("   ->", url.rsplit('/',1)[-1], "|", body.get("content"))

assert len(posts) == 3, f"expected 3 notifications, got {len(posts)}"
assert posts[2][0].endswith("THIRD"), "acct-3 did not use its own webhook"
assert "Main" in posts[0][1]["content"] and "Second" in posts[1][1]["content"]

print("\n--- cycle 2 (same queues; nothing should fire) ---")
posts.clear()
for w in ws: w.poll()
print("discord posts:", len(posts))
assert len(posts) == 0, "re-notified unchanged tasks!"

print("\n--- cycle 3 (new task for acct-2 only) ---")
ws[1].browser = FakeBrowser(queues["acct-2"] + [{"id":"b2","title":"Gamma","detail":""}])
for w in ws: w.poll()
print("discord posts:", len(posts))
assert len(posts) == 1 and "Second" in posts[0][1]["content"], "wrong account fired"

print("\nseen files written:", sorted(p.name for p in pathlib.Path(tmp).glob("seen-*.json")))
d1 = json.loads(pathlib.Path(tmp, "seen-acct-1.json").read_text())["seen"]
d3 = json.loads(pathlib.Path(tmp, "seen-acct-3.json").read_text())["seen"]
assert "a1" in d1 and "a1" in d3, "shared id must be tracked independently per account"
print("shared task id 'a1' tracked separately for acct-1 and acct-3: OK")

print("\n--- malformed accounts.json ---")
for bad, why in [
    ([{"name": "../escape"}], "path traversal in name"),
    ([{"name": "dup"}, {"name": "dup"}], "duplicate names"),
    ([], "empty array"),
    ("notalist", "not an array"),
]:
    pathlib.Path("accounts.json").write_text(json.dumps(bad))
    try:
        accounts.load(); print(f"  FAIL: accepted {why}")
    except accounts.AccountsError as e:
        print(f"  rejected {why}: {str(e)[:60]}")

print("\nALL ASSERTIONS PASSED")
