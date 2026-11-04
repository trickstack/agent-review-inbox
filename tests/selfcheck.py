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
       "REVIEW_INBOX_BEAT_S": "0.5", "REVIEW_INBOX_PORT": PORT, "CLAUDE_CODE_SESSION_ID": "s1",
       "REVIEW_INBOX_HOSTNAME": "box.tailnet.ts.net"}
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
assert open(os.path.join(tmp, ".gitignore")).read().strip() == "*"  # the inbox ignores itself

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
    # only this page may talk to the server: a foreign Host (DNS rebinding), a foreign Origin, or a non-JSON post is refused
    def code(path, data=None, **hdr):
        try:
            return urllib.request.urlopen(urllib.request.Request(base + path, data, hdr)).status
        except urllib.error.HTTPError as e:
            return e.code
    js = {"Content-Type": "application/json"}
    nope = json.dumps({"id": item["id"], "status": "ok"}).encode()
    assert code("/api/review", Host="evil.example:7799") == 403
    assert code("/api/review", nope, Origin="https://evil.example", **js) == 403
    # behind a trusted proxy (tailscale serve passes the browser's Host through and the page's Origin is https)
    ts = {"Host": "box.tailnet.ts.net:8443", "Origin": "https://box.tailnet.ts.net:8443"}
    assert code("/api/review", Host="box.tailnet.ts.net") == 200
    assert code("/api/review", b"{}", **ts, **js) == 400  # past the guard: refused only for the bad body
    assert code("/api/review", Host="box.tailnet.ts.net.evil.example") == 403
    assert code("/api/review", nope, Host="box.tailnet.ts.net", Origin="https://evil.example", **js) == 403
    assert code("/api/review", nope, **{"Content-Type": "text/plain"}) == 415
    assert code("/api/review", json.dumps({"id": item["id"], "status": "answered"}).encode(), **js) == 400  # not for a review
    assert review.items()[0]["status"] == "open"
    # attached media can't run as a page: nosniff, and anything but image/video/audio is a download
    html = os.path.join(tmp, "x.html")
    open(html, "w").write("<script>fetch('/api/review')</script>")
    hi = run("add", "--title", "Has html", "--media", html, png).stdout.splitlines()[0]
    r = urllib.request.urlopen(f"{base}/media/{os.path.basename(hi)}/x.html")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Content-Disposition"] == "attachment"
    r = urllib.request.urlopen(f"{base}/media/{os.path.basename(hi)}/still.png")
    assert r.headers["Content-Disposition"] is None
    run("revoke", os.path.basename(hi))
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

# an OK with a note: committing doesn't close it, acting on the note does
ok_id = os.path.basename(run("add", "--title", "Wave hits", "--media", png).stdout.splitlines()[0])
review.answer(ok_id, "ok", "good, but rework the spray and add a hull-slap sound")
assert "This OK has a note" in hook("UserPromptSubmit")
out = run("handled", ok_id, "committed abc123").stdout
assert "isn't done yet" in out and "hull-slap" in out, out
assert "--did" in hook("Stop")
run("handled", ok_id, "--did", "reworked the spray, posted a new take; hull-slap sound started")
assert hook("Stop") == ""
plain = os.path.basename(run("add", "--title", "Plain OK", "--media", png).stdout.splitlines()[0])
review.answer(plain, "ok", "")
hook("UserPromptSubmit")
assert "isn't done" not in run("handled", plain, "committed def456").stdout and hook("Stop") == ""
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
# a question: options on the page, the answer comes back through `wait` like a verdict
q = subprocess.run([sys.executable, CLI, "--session", "q1", "ask", "Red or blue paddle?", "--options", "Red", "Blue"], env=env,
                   capture_output=True, text=True).stdout
qi = next(i for i in review.items() if i["title"] == "Red or blue paddle?")
assert qi["kind"] == "question" and qi["options"] == ["Red", "Blue"] and qi["sid"] == "q1", q
qw = subprocess.Popen([sys.executable, CLI, "--session", "q1", "wait"], env=env, stdout=subprocess.PIPE, text=True)
time.sleep(1.5)
review.answer(qi["id"], "answered", "Blue")
got, _ = qw.communicate(timeout=10)
assert qw.returncode == 0 and 'ANSWER to your question "Red or blue paddle?"' in got and "Blue" in got, got
# answered in the chat instead: the next prompt closes the session's open questions (not its reviews), without a verdict
def prompt(sid, text):
    return subprocess.run([sys.executable, CLI, "hook"], env=env, capture_output=True, text=True,
                          input=json.dumps({"session_id": sid, "hook_event_name": "UserPromptSubmit", "prompt": text})).stdout


subprocess.run([sys.executable, CLI, "--session", "q3", "ask", "Asked before any prompt?"], env=env, capture_output=True)
time.sleep(1.1)  # ids have whole seconds: in the same second as the prompt it would count as asked after it
prompt("q3", "hi")  # the session's first prompt seen: closes nothing, so that question stays up for the inbox
time.sleep(1.1)
subprocess.run([sys.executable, CLI, "--session", "q3", "ask", "Bigger ball?"], env=env, capture_output=True)
subprocess.run([sys.executable, CLI, "--session", "q3", "add", "--title", "Paddle glow"], env=env, capture_output=True)
said = subprocess.run([sys.executable, CLI, "hook"], env=env, capture_output=True, text=True,
                      input=json.dumps({"session_id": "q3", "hook_event_name": "UserPromptSubmit", "prompt": "yes, twice as big"})).stdout
q3 = {i["title"]: i for i in review.items() if i["sid"] == "q3"}
assert q3["Bigger ball?"]["note"] == "answered in the chat: yes, twice as big" and not q3["Paddle glow"].get("answered")
assert not q3["Asked before any prompt?"].get("answered")  # older than the previous prompt: only the inbox answers it
review.answer(q3["Asked before any prompt?"]["id"], "dismissed", "")
assert "Bigger ball?" not in said, said


def stop_after(text, active=False, sid="q2", transcript=None):
    ev = {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": active}
    if transcript:
        ev["transcript_path"] = transcript
    else:
        ev["last_assistant_message"] = text
    return subprocess.run([sys.executable, CLI, "hook"], env=env, input=json.dumps(ev), capture_output=True, text=True).stdout


# a turn that ends on a question asked in chat is pushed back once, never twice; a plain ending is left alone.
# Only in a session that has used the inbox: a session that never posted is never nagged.
assert stop_after("Want it brighter, or keep it as is?", sid="fresh") == ""
prompt("q2", "start")
time.sleep(1.1)
subprocess.run([sys.executable, CLI, "--session", "q2", "ask", "Warm-up?"], env=env, capture_output=True)
subprocess.run([sys.executable, CLI, "hook"], env=env, capture_output=True, text=True,
               input=json.dumps({"session_id": "q2", "hook_event_name": "UserPromptSubmit", "prompt": "sure"}))
assert "review.py" in stop_after("Done with the trail. Want it brighter, or keep it as is?") and "ask" in stop_after("Which colour?")
assert stop_after("Which colour?", active=True) == ""
assert stop_after("Done: the trail is shorter now.") == ""
assert not review.asks_human("Fixed the `?q=` parser or the redirect, see https://x.test/a?b=1 for details.")
assert not review.asks_human("Tried red or blue? Neither worked, so it stays grey.")  # "or" alone isn't asking
assert review.asks_human("Should I also add particles? Say the word and I'll start.")
assert subprocess.run([sys.executable, CLI, "hook"], env={**env, "REVIEW_INBOX_BOARD_ONLY": "1"}, capture_output=True, text=True,
                      input=json.dumps({"session_id": "q2", "hook_event_name": "Stop", "last_assistant_message": "Which colour?"})).stdout == ""
tr = os.path.join(tmp, "t.jsonl")
with open(tr, "w") as f:
    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Should I also add particles?"}]}}) + "\n")
assert "ask" in stop_after("", transcript=tr)
assert stop_after("", transcript=os.path.join(tmp, "missing.jsonl")) == ""  # fails open

# scripted steps: ';' splits --do, a --script splits on lines only (so a typed ';' survives), bad steps fail fast
assert review.parse_steps("click text=Save; type hi there ;key Control+S; wait 300") == [
    ("click", "text=Save"), ("type", "hi there"), ("key", "Control+S"), ("wait", "300")]
assert review.parse_steps("type a; b\n# note\nclick 10, 20", "\n") == [("type", "a; b"), ("click", "10, 20")]
assert review.xy("10, 20") == (10, 20) and review.xy("#save") is None
for bad in ("jump 3", "click"):
    try:
        review.parse_steps(bad)
        raise AssertionError(bad)
    except SystemExit:
        pass

# doctor: exit 0 with everything present, 1 with something missing; each missing tool says what it disables and how to install
r = subprocess.run([sys.executable, CLI, "doctor"], env=env, capture_output=True, text=True)
lines = r.stdout.splitlines()
assert r.returncode in (0, 1) and lines, r
if r.returncode:
    assert lines[-1].endswith("the rest of the inbox works without them")
    assert sum(l.startswith("missing  ") for l in lines) == len([l for l in lines if l.startswith("         install:  ")]) > 0
else:
    assert lines == ["all capture tools present"]

# no transcript to read: `wait` still gives up before the cache would go cold, timed from when it began
quick = subprocess.run([sys.executable, CLI, "--session", "c1", "wait"], env={**env, "REVIEW_INBOX_WARM_MIN": "5.01"},
                       capture_output=True, text=True, timeout=30)
assert quick.returncode == 3, quick.stdout

# a missing --media path fails before any folder exists; two posts with one title in one second both land
before = set(os.listdir(tmp))
r = run("add", "--title", "Typo", "--media", os.path.join(tmp, "nope.png"))
assert r.returncode and "no such file" in r.stderr and set(os.listdir(tmp)) == before, r
a1, _ = review.add("review", "Twin")
a2, _ = review.add("review", "Twin")
assert a1["id"] != a2["id"]
long, _ = review.add("review", "Brick burst, take 2: 0.3 s shorter and brighter")
assert long["id"].endswith("-brick-burst-take-2"), long["id"]  # four whole words, never a word cut in half
# revoke and handled: only your own items, and a clean error for an unknown id
r = run("--session", "someone-else", "revoke", a1["id"])
assert r.returncode and "another session" in r.stderr and os.path.isdir(os.path.join(tmp, a1["id"])), r
r = run("handled", "20000101-000000-nothing", "x")
assert r.returncode and "no item" in r.stderr and "Traceback" not in r.stderr, r
# the CLI handed to the agent names the inbox, so it finds the same one as the hooks wherever it runs
assert f'--dir "{review.shown(tmp)}"' in hook("SessionStart") and "\\" not in hook("SessionStart").split("CLI")[1]  # one slash style
other = tempfile.mkdtemp()
subprocess.run([sys.executable, CLI, "--dir", tmp, "--session", "d1", "add", "--title", "From elsewhere"], env={**ENV, "REVIEW_INBOX_DIR": other},
               capture_output=True)
assert any(i["title"] == "From elsewhere" for i in review.items()) and not os.listdir(other)
# hook output is UTF-8 whatever the console code page: the human's note is the payload
u, _ = review.add("review", "Unicode")
review.answer(u["id"], "redo", "gör 日本 🙂")
raw = subprocess.run([sys.executable, CLI, "hook"], env=ENV, capture_output=True,
                     input=json.dumps({"session_id": "s1", "hook_event_name": "UserPromptSubmit"}).encode()).stdout
assert "gör 日本 🙂" in raw.decode("utf-8"), raw

# one inbox per user: two projects post without configuring anything, both show on one page, labelled, and each
# verdict goes back only to the session that posted it
home = tempfile.mkdtemp()
base_env = {k: v for k, v in ENV.items() if k not in ("REVIEW_INBOX_DIR", "CLAUDE_CODE_SESSION_ID")}
base_env.update(HOME=home, USERPROFILE=home, REVIEW_INBOX_PORT="7798")
for proj, sid in (("game", "pa"), ("site", "pb")):
    pdir = os.path.join(home, "work", proj)
    os.makedirs(pdir)
    subprocess.run([sys.executable, CLI, "--session", sid, "add", "--title", f"From {proj}"], env={**base_env, "CLAUDE_PROJECT_DIR": pdir},
                   capture_output=True, cwd=pdir)
shared = os.path.join(home, ".review-inbox")
srv = subprocess.Popen([sys.executable, CLI, "serve"], env=base_env, stdout=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            both = json.load(urllib.request.urlopen("http://127.0.0.1:7798/api/review", timeout=1))
            break
        except OSError:
            time.sleep(0.1)
finally:
    srv.terminate()
assert {(i["title"], i["project"], i["sid"]) for i in both} == {("From game", "game", "pa"), ("From site", "site", "pb")}, both
assert all(os.path.normcase(i["project_dir"]) == os.path.normcase(os.path.join(home, "work", i["project"])) for i in both)
site = next(i for i in both if i["project"] == "site")
subprocess.run([sys.executable, CLI, "--dir", shared, "dismiss", site["id"], "already fine"], env=base_env, capture_output=True)
said = lambda sid: subprocess.run([sys.executable, CLI, "hook"], env=base_env, capture_output=True, text=True,
                                  input=json.dumps({"session_id": sid, "hook_event_name": "UserPromptSubmit"})).stdout
assert said("pa") == "" and "From site" in said("pb")

# frames: every frame around a short moment, tiled; a moment past the end says so in one line
if review.ffmpeg():
    mp4 = os.path.join(tmp, "count.mp4")
    subprocess.run([review.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=3",
                    "-pix_fmt", "yuv420p", mp4], check=True)
    r = run("frames", mp4, "--at", "1.5")
    assert r.returncode == 0 and "15 frames" in r.stdout and os.path.getsize(os.path.join(tmp, "frames_1.5s.jpg")) > 0, r
    r = run("frames", mp4, "--at", "9")
    assert r.returncode and r.stderr.strip() == "no frames were made; is --at inside the clip?", r.stderr

# screen and window capture is opt-in: without REVIEW_INBOX_DESKTOP=1 it refuses and names the alternatives
for cmd in (("clip", "3"), ("shot",), ("clip", "--window", "X", "--do", "click 1,1")):
    r = run(*cmd)
    assert r.returncode and "REVIEW_INBOX_DESKTOP=1" in r.stderr and "--url" in r.stderr, (cmd, r.stderr)

# one recording at a time: a second waits for the first, then gives up; a dead holder's lock is taken over
code = ("import sys; sys.path.insert(0, sys.argv[1]); import review; review.LOCK_WAIT_S = 1.5" + chr(10) +
        "with review.recording_lock(): print('got it')")
with review.recording_lock():
    r = subprocess.run([sys.executable, "-c", code, os.path.dirname(CLI)], env=ENV, capture_output=True, text=True, timeout=30)
assert r.returncode and "is recording" in r.stdout and "still running" in r.stderr, (r.stdout, r.stderr)
dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True).stdout.strip()
open(os.path.join(tmp, ".recording.lock"), "w").write(dead)
r = subprocess.run([sys.executable, "-c", code, os.path.dirname(CLI)], env=ENV, capture_output=True, text=True, timeout=30)
assert r.returncode == 0 and r.stdout.strip() == "got it" and not os.path.exists(os.path.join(tmp, ".recording.lock")), r

# retention: answered items older than KEEP_DAYS lose their media but keep their text; open and recent ones keep it all
def aged(title, answered_days_ago):
    it, d = review.add("review", title, media=[png])
    if answered_days_ago is not None:
        it = review.answer(it["id"], "ok", "fine")
        it["answered"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - answered_days_ago * 86400))
        review.save(it)
    return it["id"], d
old_id, old_d = aged("Old verdict", 8)
new_id, new_d = aged("Recent verdict", 2)
open_id, open_d = aged("Still open", None)
os.utime(os.path.join(open_d, "item.json"), (time.time() - 30 * 86400,) * 2)
review.prune(force=True)
gone = review.load(old_id)
assert sorted(os.listdir(old_d)) == ["item.json"] and gone["pruned"] and gone["note"] == "fine" and gone["status"] == "ok"
assert "still.png" in os.listdir(new_d) and "still.png" in os.listdir(open_d)
review.KEEP_DAYS = 0
review.prune(force=True)
assert "still.png" in os.listdir(new_d)

# session board: a turn that asked nothing and left nothing open is done (and leaves after DONE_S); a question keeps it up
def turn(sid, *assistant):
    t = os.path.join(tmp, sid + ".jsonl")
    with open(t, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        for content in assistant:
            f.write(json.dumps(content if isinstance(content, dict) else {"type": "assistant", "message": {"content": content}}) + "\n")
    ev = {"session_id": sid, "transcript_path": t, "cwd": tmp}
    review.track({**ev, "hook_event_name": "UserPromptSubmit", "prompt": "go"}, False)
    review.track({**ev, "hook_event_name": "Stop"}, False)
    return {s["sid"]: s for s in review.sessions()["sessions"]}.get(sid)
fin = turn("b-done", [{"type": "text", "text": "Committed 1a2b3c, tests pass."},
                      {"type": "tool_use", "name": "mcp__ccd_turn__wrap_up", "input": {"recap": "Shipped."}}])
assert fin["done"] and not fin["asks"], fin
box = turn("b-box", [{"type": "tool_use", "name": "mcp__ccd_turn__wrap_up", "input": {"recap": "Ready.", "ask": "Commit and push?"}}],
           [{"type": "text", "text": "Ready to ship."}])
assert box["asks"] == "Commit and push?" and not box["done"], box
chat = turn("b-chat", [{"type": "text", "text": "Fixed it.\n\nShould I also update the README?"}])
assert chat["asks"].endswith("README?") and not chat["done"], chat
meta = turn("b-meta", [{"type": "tool_use", "name": "mcp__ccd_turn__wrap_up", "input": {"recap": "Ready.", "ask": "Merge it?"}}],
            {"type": "user", "isMeta": True, "message": {"content": [{"type": "text", "text": "Stop hook feedback: run wait"}]}},
            [{"type": "text", "text": "Done waiting."}])
assert meta["asks"] == "Merge it?", meta  # a hook's message in the transcript isn't the human's prompt
p = os.path.join(review.SESSIONS, "b-done.json")
s = json.load(open(p))
s["last"] -= review.DONE_S + 1
json.dump(s, open(p, "w"))
ids = [s["sid"] for s in review.sessions()["sessions"]]
assert "b-done" not in ids and "b-box" in ids, ids
review.hide_session("b-box")
assert "b-box" not in [s["sid"] for s in review.sessions()["sessions"]]
p = os.path.join(review.SESSIONS, "b-chat.json")  # a question nobody answered before the cache went cold leaves the board
s = json.load(open(p))
s["last"] -= review.WARM_MIN * 60 + 1
json.dump(s, open(p, "w"))
assert "b-chat" not in [s["sid"] for s in review.sessions()["sessions"]]

print("selfcheck ok")
