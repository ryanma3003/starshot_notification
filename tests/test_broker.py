"""Exercise broker-mode extraction against real captured HTML + synthetic states."""
import os, sys, tempfile, pathlib

tmp = tempfile.mkdtemp()
os.environ.update({
    "STATE_DIR": tmp, "DISCORD_WEBHOOK_URL": "https://hook/X",
    "EXTRACT_MODE": "broker", "CLICK_RETRY": "false",
    "BROKER_SETTLE_SECONDS": "3",
})
import pathlib as _pl
ROOT = _pl.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config, scraper
from scraper import Browser, ExtractionError

EMPTY_HTML = (ROOT / "tests/fixtures/empty_queue.html").read_text()

TASK_HTML = """
<div id="root"><div>
  <h1 aria-label="Show info about this task (TASK-8842)">Segment Review</h1>
  <button id="starshot_submit_button" type="button">Done</button>
  <div role="main">work goes here</div>
</div></div>
"""

BROKEN_HTML = """<div id="root"><div><h2>502 Bad Gateway</h2></div></div>"""

# The shell as it looks mid-load: the Done button is present but DISABLED, the
# h1 carries "(undefined)", and the empty-queue modal has not painted yet. This
# is what polling immediately after sign-in sees, and it must not read as a task.
LOADING_HTML = """
<div id="root"><div>
  <h1 aria-label="Show info about this task (undefined)"></h1>
  <button id="starshot_submit_button" type="button" disabled="">Done</button>
  <div role="main"></div>
</div></div>
"""

# Same, but the modal arrives a moment later - the real behaviour.
LATE_MODAL_HTML = LOADING_HTML + """
<script>
  setTimeout(function () {
    var d = document.createElement('div');
    d.setAttribute('role', 'dialog');
    d.innerHTML = '<h2>User, there are no available tasks at the moment.</h2>';
    document.body.appendChild(d);
  }, 1200);
</script>
"""

BLANK_HTML = """<div id="root"></div>"""


def run(html, label):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = b.new_page()
        page.set_content(html)
        br = Browser.__new__(Browser)          # bypass __enter__; we supply the page
        br.page, br.label = page, label
        try:
            return br._tasks_from_broker()
        finally:
            b.close()


print("=== empty queue (REAL captured page) ===")
tasks = run(EMPTY_HTML, "acct-1")
assert tasks == [], f"expected no tasks, got {tasks}"
print("  -> 0 tasks. correct")

print("\n=== task available ===")
tasks = run(TASK_HTML, "acct-1")
assert len(tasks) == 1, f"expected 1 task, got {tasks}"
assert tasks[0]["id"] == "TASK-8842", f"id not read from aria-label: {tasks[0]}"
assert tasks[0]["title"] == "Segment Review"
print(f"  -> 1 task: id={tasks[0]['id']!r} title={tasks[0]['title']!r}. correct")

print("\n=== error page must NOT read as 'task available' ===")
try:
    tasks = run(BROKEN_HTML, "acct-1")
    print(f"  FAIL: returned {tasks}")
    sys.exit(1)
except ExtractionError as e:
    print(f"  -> refused to guess: {str(e)[:70]}...")

print("\n=== blank app shell must NOT read as 'task available' ===")
try:
    tasks = run(BLANK_HTML, "acct-1")
    print(f"  FAIL: returned {tasks}")
    sys.exit(1)
except ExtractionError as e:
    print(f"  -> refused to guess: {str(e)[:70]}...")

print("\n=== REGRESSION: mid-load shell must NOT report a task ===")
# The Done button always exists in the DOM; it is merely disabled when no task
# is loaded. Treating its presence as evidence produced false notifications.
try:
    tasks = run(LOADING_HTML, "acct-3")
    print(f"  FAIL: reported {tasks}")
    sys.exit(1)
except ExtractionError as e:
    print(f"  -> refused to guess: {str(e)[:64]}...")

print("\n=== REGRESSION: modal arriving late resolves to 'no tasks' ===")
tasks = run(LATE_MODAL_HTML, "acct-3")
assert tasks == [], f"expected no tasks once the modal painted, got {tasks}"
print("  -> waited for the page to settle, then reported 0 tasks. correct")

print("\n=== enabled Done button counts as a task even without an id ===")
ENABLED_NO_ID = """
<div id="root"><div>
  <h1 aria-label="Show info about this task (undefined)">Some Task</h1>
  <button id="starshot_submit_button" type="button">Done</button>
</div></div>
"""
tasks = run(ENABLED_NO_ID, "acct-3")
assert len(tasks) == 1, f"expected 1 task, got {tasks}"
print(f"  -> 1 task: {tasks[0]['title']!r}. correct")

print("\n=== id stability across polls (same task -> same id) ===")
a = run(TASK_HTML, "x")[0]["id"]
b = run(TASK_HTML, "x")[0]["id"]
assert a == b, "id changed between polls -> would re-notify forever"
print(f"  -> stable: {a}")

print("\nALL BROKER ASSERTIONS PASSED")
