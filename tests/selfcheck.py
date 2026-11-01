"""One runnable check of the whole loop: post -> wait -> answer -> verdict delivered -> Stop hook demands action -> handled.
Also: a waiter that died (stale waiting.json, even with a live pid) isn't counted, and the page only serves an item's own files.

    python tests/selfcheck.py
"""
import json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "..", "skills", "review-inbox", "review.py")
tmp = tempfile.mkdtemp()
PORT = "7799"
ENV = {**os.environ, "REVIEW_INBOX_DIR": tmp, "REVIEW_INBOX_NO_TOAST": "1", "REVIEW_INBOX_NO_SERVER": "1",
       "REVIEW_INBOX_BEAT_S": "0.5", "REVIEW_INBOX_PORT": PORT, "CLAUDE_CODE_SESSION_ID": "s1"}
os.environ.update(ENV)
sys.path.insert(0, os.path.dirname(CLI))
import review  # noqa: E402  (reads the env above)


def run(*args, stdin=None):
    return subprocess.run([sys.executable, CLI, *args], env=ENV, input=stdin, capture_output=True, text=True, timeout=30)


def hook(event):
    return run("hook", stdin=json.dumps({"session_id": "s1", "hook_event_name": event})).stdout


png = os.path.join(tmp, "still.png")
open(png, "wb").write(b"\x89PNG\r\n\x1a\n")
out = run("add", "--title", "Ball trail", "--how", "watch the ball", "--media", png).stdout
item = review.items()[0]
assert item["status"] == "open" and item["sid"] == "s1" and item["media"] == ["still.png"], out

# Stop with an open item and nothing waiting -> asked to arm `wait`
assert "nothing waiting" in hook("Stop")

# a dead waiter's file stays behind; a live pid in it (ours) doesn't make it count once it's stale
review.mark(item["id"], "waiting.json", json.dumps({"pid": os.getpid()}))
os.utime(os.path.join(tmp, item["id"], "waiting.json"), (time.time() - 60,) * 2)
assert not review.waiting(item["id"]), "stale waiting.json counted"

w = subprocess.Popen([sys.executable, CLI, "wait"], env=ENV, stdout=subprocess.PIPE, text=True)
time.sleep(1.5)
assert review.waiting(item["id"]), "live waiter not seen"
assert hook("Stop") == "", "Stop blocked although a wait is armed"

# the page: list, serve own media, refuse anything else
srv = subprocess.Popen([sys.executable, CLI, "serve"], env=ENV, stdout=subprocess.DEVNULL)
try:
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(50):
        try:
            listed = json.load(urllib.request.urlopen(base + "/api/review", timeout=1))
            break
        except OSError:
            time.sleep(0.1)
    assert listed[0]["id"] == item["id"] and listed[0]["waiting"] is True
    assert urllib.request.urlopen(f"{base}/media/{item['id']}/still.png").read().startswith(b"\x89PNG")
    for bad in (f"/media/{item['id']}/item.json", "/media/..%2F..%2Fx/still.png"):
        try:
            urllib.request.urlopen(base + bad)
            raise AssertionError("served " + bad)
        except urllib.error.HTTPError as e:
            assert e.code == 404
    # the human answers on the page
    req = urllib.request.Request(base + "/api/review", json.dumps({"id": item["id"], "status": "redo", "note": "too faint"}).encode(),
                                 {"Content-Type": "application/json"})
    assert json.load(urllib.request.urlopen(req))["status"] == "redo"
finally:
    srv.terminate()

got, _ = w.communicate(timeout=10)
assert w.returncode == 0 and "ANSWERED REDO: Ball trail" in got and "too faint" in got, got
assert not review.waiting(item["id"])

# delivered by `wait`, so the prompt hook stays quiet; but the turn can't end until the REDO is acted on
assert hook("UserPromptSubmit") == ""
assert "haven't acted" in hook("Stop")
run("handled", item["id"], "reworked, retake posted")
assert hook("Stop") == ""
assert "review inbox" in hook("SessionStart")

# an agent without CLAUDE_CODE_SESSION_ID (Codex): the SessionStart hook hands it a CLI carrying --session
env = {k: v for k, v in ENV.items() if k != "CLAUDE_CODE_SESSION_ID"}
start = subprocess.run([sys.executable, CLI, "hook"], env=env, input=json.dumps({"session_id": "c1", "hook_event_name": "SessionStart"}),
                       capture_output=True, text=True).stdout
assert "--session c1" in start, start
subprocess.run([sys.executable, CLI, "--session", "c1", "add", "--title", "Codex take"], env=env, capture_output=True)
assert next(i for i in review.items() if i["title"] == "Codex take")["sid"] == "c1"
stop = subprocess.run([sys.executable, CLI, "hook"], env=env, input=json.dumps({"session_id": "c1", "hook_event_name": "Stop"}),
                      capture_output=True, text=True).stdout
assert "nothing waiting" in stop and "--session c1 wait" in stop, stop
# no transcript to read: `wait` still gives up before the cache would go cold, timed from when it began
quick = subprocess.run([sys.executable, CLI, "--session", "c1", "wait"], env={**env, "REVIEW_INBOX_WARM_MIN": "5.01"},
                       capture_output=True, text=True, timeout=30)
assert quick.returncode == 3, quick.stdout

print("selfcheck ok")
