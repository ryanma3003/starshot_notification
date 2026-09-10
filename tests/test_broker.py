"""Exercise broker-mode extraction against real captured HTML + synthetic states."""
import os, sys, tempfile, pathlib

tmp = tempfile.mkdtemp()
os.environ.update({
    "STATE_DIR": tmp, "DISCORD_WEBHOOK_URL": "https://hook/X",
    "EXTRACT_MODE": "broker", "CLICK_RETRY": "false",
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

print("\n=== id stability across polls (same task -> same id) ===")
a = run(TASK_HTML, "x")[0]["id"]
b = run(TASK_HTML, "x")[0]["id"]
assert a == b, "id changed between polls -> would re-notify forever"
print(f"  -> stable: {a}")

print("\nALL BROKER ASSERTIONS PASSED")
