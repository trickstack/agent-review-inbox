"""Stress test: many agent sessions on one inbox at once. Every verdict must reach exactly the session that posted the
item, once, and nobody else; the inbox must stay consistent under concurrent posts, answers, waits and hooks.

    python tests/stress.py [rounds]      (default 5; a temp inbox and its own server on a free port, never ~/.review-inbox)

Per round: 8 sessions over 3 projects post at once (12 items titled alike, 4 questions), each arms `wait`, the page
answers one item per session (shuffled, in pairs at the same moment) while Stop hooks fire alongside, then the real
hooks.json command runs per session: deliveries, Stop blocks, chat replies closing questions, handled, revoke.
"""
import json, os, random, re, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request, uuid
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "skills", "review-inbox", "review.py")
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
tmp = tempfile.mkdtemp()
INBOX = os.path.join(tmp, "inbox")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
ENV = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID", "REVIEW_INBOX_SESSION", "CLAUDE_PROJECT_DIR")}
ENV.update(REVIEW_INBOX_DIR=INBOX, REVIEW_INBOX_PORT=str(PORT), REVIEW_INBOX_NO_TOAST="1", REVIEW_INBOX_NO_SERVER="1",
           REVIEW_INBOX_BEAT_S="1", CLAUDE_CONFIG_DIR=os.path.join(tmp, "claude"), PYTHONIOENCODING="utf-8")
os.environ.update(REVIEW_INBOX_DIR=INBOX, REVIEW_INBOX_BEAT_S="1")
sys.path.insert(0, os.path.dirname(CLI))
import review  # noqa: E402  (reads the env above)

PROJECTS = [os.path.join(tmp, p) for p in ("alpha", "beta", "gamma")]
for p in PROJECTS:
    os.makedirs(p)

# the real hook command from hooks.json, run by Git Bash as Claude Code runs it (never WSL's bash.exe)
with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
    HOOKS = json.load(f)["hooks"]
HOOK_CMD = HOOKS["Stop"][0]["hooks"][0]["command"]
assert HOOKS["UserPromptSubmit"][0]["hooks"][0]["command"] == HOOK_CMD
BASH = shutil.which("bash")
if os.name == "nt":
    git = os.path.dirname(shutil.which("git") or "")  # Git\cmd or Git\mingw64\bin: Git Bash is Git\bin\bash.exe
    BASH = next((b for b in (os.path.join(git, *[".."] * n, "bin", "bash.exe") for n in (1, 2)) if git and os.path.isfile(b)), None)
if not BASH:
    print("no bash: hooks run as `review.py hook` directly")


def cli(sid, proj, *args, timeout=30):
    r = subprocess.run([sys.executable, CLI, "--session", sid, *args], env={**ENV, "CLAUDE_PROJECT_DIR": proj}, cwd=proj,
                       capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return r


def hook(sid, event, direct=False, **extra):
    ev = json.dumps({"session_id": sid, "hook_event_name": event, **extra})
    cmd = [BASH, "-c", HOOK_CMD] if BASH and not direct else [sys.executable, CLI, "hook"]
    t = time.time()
    r = subprocess.run(cmd, env={**ENV, "CLAUDE_PLUGIN_ROOT": ROOT.replace("\\", "/")}, input=ev, capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    slow.append((round(time.time() - t, 1), event, sid)) if time.time() - t > 10 else None
    assert r.returncode == 0 and "skipped" not in r.stderr, (event, sid, r.stderr)
    return r.stdout


def ids_in(text):
    return set(re.findall(r"\d{8}-\d{6}-[a-z0-9-]+", text))


def api(path="/api/review", body=None):
    """As page.html does: same-origin fetch, JSON POST."""
    hdr = {"Content-Type": "application/json", "Origin": BASE} if body is not None else {}
    data = json.dumps(body).encode() if body is not None else None
    return json.load(urllib.request.urlopen(urllib.request.Request(BASE + path, data, hdr), timeout=10))


def folder_items():
    """Every item.json on disk must parse: a torn write here is a corrupted inbox."""
    out = {}
    for name in os.listdir(INBOX):
        p = os.path.join(INBOX, name, "item.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                out[name] = json.load(f)
    return out


slow = []  # hooks.json gives a hook 20 s; one slower than 10 s here is reported
ACT = "The human answered in the review inbox"
stats = dict(sessions=0, items=0, answers=0, hooks=0, waits=0, listings=0)


def round_(r):
    tag = uuid.uuid4().hex[:6]
    S = [f"r{r}-s{k}-{tag}" for k in range(8)]
    proj = {s: PROJECTS[k % 3] for k, s in enumerate(S)}
    stats["sessions"] += len(S)

    # 1. everyone posts at once: 12 x "Ball trail" (two per odd session), a question per even session
    jobs = [(s, ("add", "--title", "Ball trail", "--how", "watch the ball")) for s in S]
    jobs += [(s, ("ask", "Red or blue paddle?", "--options", "Red", "Blue")) if k % 2 == 0 else
             (s, ("add", "--title", "Ball trail")) for k, s in enumerate(S)]
    random.shuffle(jobs)
    with ThreadPoolExecutor(len(jobs)) as ex:
        for res in ex.map(lambda j: cli(j[0], proj[j[0]], *j[1]), jobs):
            assert res.returncode == 0, res.stderr
    on_disk = folder_items()
    own = {s: sorted((i for i in on_disk.values() if i["sid"] == s), key=lambda i: i["id"]) for s in S}
    for k, s in enumerate(S):
        kinds = sorted(i["kind"] for i in own[s])
        assert kinds == (["question", "review"] if k % 2 == 0 else ["review", "review"]), (s, kinds)
        assert all(i["project"] == os.path.basename(proj[s]) and i["status"] == "open" for i in own[s])
    ids = {i["id"]: s for s in S for i in own[s]}
    stats["items"] += len(ids)
    R = {s: next(i["id"] for i in own[s] if i["kind"] == "review") for s in S}
    Q = {s: next(i["id"] for i in own[s] if i["kind"] == "question") for s in S[::2]}
    R2 = {s: [i["id"] for i in own[s]][1] for s in S[1::2]}

    time.sleep(1.1)  # a human answers seconds later, not in the second of the post (that second is ambiguous by design)

    # 2. every session arms `wait` (no id: all its open items)
    waits = {s: subprocess.Popen([sys.executable, CLI, "--session", s, "wait"], env={**ENV, "CLAUDE_PROJECT_DIR": proj[s]},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8") for s in S}
    stats["waits"] += len(S)
    t = time.time()
    while not all(review.waiting(i) for i in ids):
        assert time.time() - t < 20, "waiters never showed up"
        time.sleep(0.1)

    # 3. the page answers one item per session (sessions 0 and 4: their question), shuffled, two at the same moment,
    #    while the page keeps listing and Stop hooks fire for random sessions (they must leave a waited item to `wait`)
    target = {s: (Q[s] if k in (0, 4) else R[s]) for k, s in enumerate(S)}
    notes = {}
    for k, s in enumerate(S):
        i = target[s]
        kind = "question" if k in (0, 4) else "review"
        notes[i] = (random.choice(["Red", "Blue"]) + " " if kind == "question" else "") + f"note {s} gör ✓ {uuid.uuid4().hex[:8]}"
    status = {i: ("answered" if i in Q.values() else random.choice(["ok", "redo"])) for i in target.values()}
    stop_now, side_out, anomalies = threading.Event(), [], []

    def lister():
        while not stop_now.is_set():
            try:
                got = {i["id"] for i in api()}
                stats["listings"] += 1
                if not set(ids) <= got:
                    anomalies.append(f"page list missed {sorted(set(ids) - got)}")
            except Exception as e:
                anomalies.append(f"list failed: {e!r}")

    def stopper():
        while not stop_now.is_set():
            s = random.choice(S)
            side_out.append((s, hook(s, "Stop", direct=True, last_assistant_message="Done.")))
            stats["hooks"] += 1

    side = [threading.Thread(target=f) for f in (lister, lister, stopper, stopper)]
    for th in side:
        th.start()
    order = list(S)
    random.shuffle(order)
    for a, b in zip(order[::2], order[1::2]):  # a pair answered in the same instant
        with ThreadPoolExecutor(2) as ex:
            for got in ex.map(lambda s: api(body={"id": target[s], "status": status[target[s]], "note": notes[target[s]]}), (a, b)):
                assert got["status"] == status[got["id"]] and got["note"] == notes[got["id"]]
        stats["answers"] += 2
    outs = {}
    for s, w in waits.items():
        out, err = w.communicate(timeout=30)
        assert w.returncode == 0, (s, w.returncode, out, err)
        outs[s] = out
    stop_now.set()
    for th in side:
        th.join()
    assert not anomalies, anomalies[:5]

    every_id = set(folder_items())
    for s in S:
        out, mine_ = outs[s], target[s]
        assert out.startswith(ACT) and mine_ in out and notes[mine_] in out, (s, out)
        assert ids_in(out) == {mine_}, (s, "other ids", out)
        assert not [n for i, n in notes.items() if i != mine_ and n in out], (s, "foreign note", out)
        assert not review.waiting(mine_)
    for s, out in side_out:  # a Stop hook beside a live `wait` never delivers; whatever it says is about its own items
        assert ACT not in out, (s, out)
        assert all(ids.get(i) == s for i in ids_in(out)), (s, out)

    # 4. the rest of the odd sessions' items get answered with nobody waiting: the hooks must hand them over
    for s in S[1::2]:
        i = R2[s]
        notes[i] = f"second {s} {uuid.uuid4().hex[:8]}"
        status[i] = random.choice(["ok", "redo"])
        api(body={"id": i, "status": status[i], "note": notes[i]})
        stats["answers"] += 1

    def foreign(s, out):
        return [i for i in ids_in(out) if ids.get(i) != s] + [n for i, n in notes.items() if ids.get(i) != s and n in out]

    def stops(**extra):
        with ThreadPoolExecutor(len(S)) as ex:
            res = dict(zip(S, ex.map(lambda s: hook(s, "Stop", last_assistant_message="Done.", **extra), S)))
        stats["hooks"] += len(S)
        for s, out in res.items():
            assert not foreign(s, out), (s, foreign(s, out), out)
        return {s: json.loads(o)["reason"] if o else "" for s, o in res.items()}

    got = stops()
    for k, s in enumerate(S):
        if k % 2:  # its second verdict, delivered now; the first went with `wait`
            assert got[s].startswith(ACT) and ids_in(got[s]) == {R2[s]} and notes[R2[s]] in got[s], (s, got[s])
        elif k in (0, 4):  # question answered; the review is open with nothing waiting
            assert "nothing waiting" in got[s] and ids_in(got[s]) == {R[s]}, (s, got[s])
        else:  # its verdict, not acted on yet
            assert "haven't acted" in got[s] and ids_in(got[s]) == {R[s]} and notes[R[s]] in got[s], (s, got[s])
    again = stops()
    for s in S[1::2]:  # exactly once: the next Stop reminds, it doesn't deliver again
        assert not again[s].startswith(ACT) and "haven't acted" in again[s], (s, again[s])

    # 5. handled / revoke on another session's item are refused and change nothing; then each acts on its own reviews
    def step5(k):
        s, other = S[k], S[(k + 1) % len(S)]
        victim = R[other]
        before = open(os.path.join(INBOX, victim, "item.json"), encoding="utf-8").read()
        h = cli(s, proj[s], "handled", victim, "not mine")
        assert h.returncode and review.marked(victim, "handled") is None, (s, victim, h.stdout, h.stderr)
        v = cli(s, proj[s], "revoke", victim)
        assert v.returncode and os.path.isfile(os.path.join(INBOX, victim, "item.json")), (s, victim, v.stdout, v.stderr)
        assert open(os.path.join(INBOX, victim, "item.json"), encoding="utf-8").read() == before
        for i in [R[s], R2.get(s)]:
            if i and status.get(i) in ("ok", "redo"):
                assert cli(s, proj[s], "handled", i, f"committed {s}").returncode == 0
    with ThreadPoolExecutor(len(S)) as ex:
        list(ex.map(step5, range(len(S))))
    got = stops()
    for k, s in enumerate(S):
        expect = {0: R[s], 4: R[s], 2: Q.get(s), 6: Q.get(s)}.get(k)  # open, nothing waiting; odd sessions owe nothing
        assert ("nothing waiting" in got[s] and ids_in(got[s]) == {expect}) if expect else got[s] == "", (s, got[s])

    # 6. a chat reply closes only that session's open questions (sessions 2 and 6); everyone else's prompt hook is quiet
    with ThreadPoolExecutor(len(S)) as ex:
        said = dict(zip(S, ex.map(lambda s: hook(s, "UserPromptSubmit", prompt=f"chat {s}"), S)))
    stats["hooks"] += len(S)
    assert all(v == "" for v in said.values()), said
    disk = folder_items()
    for k, s in enumerate(S):
        if k in (2, 6):
            assert disk[Q[s]]["status"] == "answered" and disk[Q[s]]["note"] == f"answered in the chat: chat {s}"
        if k in (0, 4):
            assert disk[R[s]]["status"] == "open" and disk[Q[s]]["note"] == notes[Q[s]]

    # 7. sessions 0 and 4 take down their open review; then no session in this round owes anything
    for s in (S[0], S[4]):
        assert cli(s, proj[s], "revoke", R[s]).returncode == 0 and not os.path.exists(os.path.join(INBOX, R[s]))
        every_id.discard(R[s])
    assert all(v == "" for v in stops().values())

    # 8. the folder is the truth: every item parses, the page lists exactly it, every answer is as the page set it
    disk = folder_items()
    assert {i["id"] for i in api()} == set(disk), "page list != folder"
    for i, n in notes.items():
        if i in disk:
            assert disk[i]["status"] == status[i] and disk[i]["note"] == n, (i, disk[i])


srv = subprocess.Popen([sys.executable, CLI, "serve"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(100):
        try:
            api()
            break
        except OSError:
            time.sleep(0.1)
    t0 = time.time()
    # torn reads: the page rewrites item.json in place while `wait` / hooks / the page read it; a reader must never fail
    probe, _ = review.add("review", "Probe")
    torn, done = [], threading.Event()

    def reader():
        while not done.is_set():
            try:
                review.load(probe["id"])
            except ValueError as e:
                torn.append(e)
    readers = [threading.Thread(target=reader) for _ in range(2)]
    for th in readers:
        th.start()
    for n in range(2000):
        review.save({**probe, "note": "x" * (n % 400)})
    done.set()
    for th in readers:
        th.join()
    assert not torn, f"{len(torn)} torn reads of item.json"
    shutil.rmtree(os.path.join(INBOX, probe["id"]))
    for r in range(ROUNDS):
        t = time.time()
        round_(r)
        print(f"round {r + 1}/{ROUNDS} ok ({time.time() - t:.1f} s)")
    if slow:
        print("slow hooks:", slow)
    print(f"stress ok in {time.time() - t0:.0f} s: " + ", ".join(f"{v} {k}" for k, v in stats.items()))
finally:
    srv.terminate()
    srv.wait()
    shutil.rmtree(tmp, ignore_errors=True)
