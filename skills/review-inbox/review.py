"""Review inbox: everything an agent made that waits on a human's eyes, ears or hands, in one place.

    review.py add --title "Ball trail" --how "Open the game, watch the ball" [--topic juice] [--media a.png b.mp4] [--kind bug]
    review.py clip 6 --url http://localhost:8080/   record a page for 6 s (Playwright) -> draft clip.mp4 + clip_sheet.jpg
    review.py clip 6 [--window "My Game"]           record the screen / a window for 6 s (ffmpeg)
    review.py shot --url http://localhost:8080/     still of a page (Playwright) -> draft shot.png
    review.py list [--open] [--topic juice]
    review.py wait [<id>]           (background) exits when the human answers -> wakes the agent; no id = every open item
                                    this session posted. Gives up (exit 3) before the session's prompt cache goes cold.
    review.py handled <id> "<what>" the verdict is dealt with without a new item ("committed abc123", or why you stopped)
    review.py revoke <id>           take your own unanswered item down (e.g. before posting a retake)
    review.py dismiss <id> "<note>" close an open item (a bug) your work fixed
    review.py serve                 the review page (also started on demand by `add`)
    review.py hook                  Claude Code hook (SessionStart, UserPromptSubmit, Stop): hands a session its verdicts

Storage: one folder per item in <project>/.review-inbox/<id>/ (item.json + media), so parallel sessions never write the
same file. Status: open -> ok | redo | dismissed (a bug stays open once the human describes it: that's the agent's cue).
Items remember the posting session (CLAUDE_CODE_SESSION_ID in Claude Code; --session or REVIEW_INBOX_SESSION elsewhere,
which the SessionStart hook hands the agent); the hook hands that session its verdicts, with the next
prompt or by blocking the end of a turn until it has read and acted on them.

Environment: REVIEW_INBOX_DIR (default <project>/.review-inbox), REVIEW_INBOX_PORT (7700), REVIEW_INBOX_HOST (127.0.0.1),
REVIEW_INBOX_WARM_MIN (50: idle minutes before a prompt cache counts as cold), REVIEW_INBOX_CLIP_CMD (custom capture
command, {out} and {seconds} are filled in), REVIEW_INBOX_NO_TOAST, REVIEW_INBOX_NO_SERVER.
"""
import argparse, base64, binascii, glob, http.server, json, os, re, shutil, subprocess, sys, time
from datetime import datetime

CLI = f'"{sys.executable}" "{os.path.abspath(__file__)}"'  # the interpreter that ran this: python3 vs python differs per OS
SID = os.environ.get("REVIEW_INBOX_SESSION") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")  # the agent session using the CLI
HOST = os.environ.get("REVIEW_INBOX_HOST") or "127.0.0.1"
PORT = int(os.environ.get("REVIEW_INBOX_PORT") or 7700)
PAGE = f"http://127.0.0.1:{PORT}/"
WARM_MIN = float(os.environ.get("REVIEW_INBOX_WARM_MIN") or 50)
BEAT_S = float(os.environ.get("REVIEW_INBOX_BEAT_S") or 5)  # `wait` polls and touches its waiting.json this often
STATUSES = ("open", "ok", "redo", "dismissed")
IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def inbox_dir():
    if os.environ.get("REVIEW_INBOX_DIR"):
        return os.path.abspath(os.environ["REVIEW_INBOX_DIR"])
    root = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not root:
        try:
            root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return os.path.join(root or os.getcwd(), ".review-inbox")


DIR = inbox_dir()


# ---------------------------------------------------------------- store

def add(kind, title, topic="", how="", media=(), by=""):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    d = os.path.join(DIR, time.strftime("%Y%m%d-%H%M%S-") + slug)
    os.makedirs(d)
    names = []
    for m in dict.fromkeys(os.path.abspath(m) for m in media):  # the same path twice = one file
        # every draft is <dir>/clip.mp4: copying by bare name would keep only the last take and show it N times.
        # A clash takes its folder's name instead (round/clip.mp4 -> round_clip.mp4).
        name, n = os.path.basename(m), 1
        while name in names:
            n += 1
            name = os.path.basename(os.path.dirname(m)) + "_" + (f"{n}_" if n > 2 else "") + os.path.basename(m)
        shutil.copy2(m, os.path.join(d, name))
        names.append(name)
    item = dict(id=os.path.basename(d), kind=kind, title=title, topic=topic, how=how, by=by,
                sid=SID,  # the poster: `hook` hands the verdict to this session
                media=names, created=time.strftime("%Y-%m-%d %H:%M"), status="open", note="")
    save(item)
    return item, d


def save(item):
    with open(os.path.join(DIR, item["id"], "item.json"), "w", encoding="utf-8") as f:
        json.dump(item, f, indent=1)


def items():
    out = []
    for name in sorted(os.listdir(DIR) if os.path.isdir(DIR) else [], reverse=True):
        try:
            with open(os.path.join(DIR, name, "item.json"), encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            pass
    return out


def waiting(item_id):
    """Is an agent's `wait` blocked on this item right now? The waiter touches waiting.json every BEAT_S, so a killed
    waiter (whose file stays behind) stops counting within seconds, and a reused pid can't fake it."""
    try:
        return time.time() - os.path.getmtime(os.path.join(DIR, item_id, "waiting.json")) < 3 * BEAT_S
    except OSError:
        return False


def answer(item_id, status, note, images=()):
    """images: data URLs the human pasted/dropped on the page -> reply_N.<ext> in the item folder, appended to item["reply"]."""
    if status not in STATUSES or item_id not in os.listdir(DIR):  # id comes from the page: never a path
        raise ValueError("bad id or status")
    item = next(i for i in items() if i["id"] == item_id)
    reply = item.setdefault("reply", [])
    for url in images:
        head, _, data = url.partition(",")
        ext = IMAGE_EXT.get(head.removeprefix("data:").removesuffix(";base64"))
        if not ext:
            raise ValueError("not an image")
        n = len(reply) + 1
        while any(r.startswith(f"reply_{n}.") for r in reply):  # a removed image leaves a gap: never overwrite a kept one
            n += 1
        name = f"reply_{n}.{ext}"
        with open(os.path.join(DIR, item_id, name), "wb") as f:
            f.write(base64.b64decode(data))
        reply.append(name)
    item.update(status=status, note=note, answered=time.strftime("%Y-%m-%d %H:%M:%S"))
    save(item)
    return item


def remove_reply(item_id, name):
    """The human took back an image they attached (X on the page)."""
    item = next(i for i in items() if i["id"] == item_id)
    if name not in item.get("reply", []):  # name comes from the page: only ever one of the item's own replies
        raise ValueError("no such image")
    item["reply"].remove(name)
    os.remove(os.path.join(DIR, item_id, name))
    save(item)
    return item


def revoke(item_id):
    """Take an unanswered item down (the agent posted it and wants a retake); answered items stay for the record."""
    d = os.path.join(DIR, os.path.basename(item_id))
    with open(os.path.join(d, "item.json"), encoding="utf-8") as f:
        item = json.load(f)
    if item.get("answered"):
        raise SystemExit(f"already answered ({item['status']}), left in place")
    shutil.rmtree(d)
    print("revoked", item["id"])


def reply_paths(item):
    return "".join(f"\nimage: {os.path.join(DIR, item['id'], r)}" for r in item.get("reply", []))


def mark(item_id, name, text=""):
    with open(os.path.join(DIR, item_id, name), "w", encoding="utf-8") as f:
        f.write(text)


def marked(item_id, name):
    try:
        with open(os.path.join(DIR, item_id, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def mine(sid):
    return [i for i in items() if sid and i.get("sid") == sid]


# ---------------------------------------------------------------- capture

def ffmpeg():
    return shutil.which("ffmpeg")


def sheet(mp4, out_dir, seconds):
    """3 fps contact sheet for the agent to Read before posting (the page doesn't show sheets to the human)."""
    cols = 6 if seconds <= 10 else 10
    rows = max(1, -(-int(seconds) * 3 // cols))
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", mp4, "-vf", f"fps=3,scale=426:-2,tile={cols}x{rows}",
                    "-frames:v", "1", "-update", "1", os.path.join(out_dir, "clip_sheet.jpg")])


def playwright_page(url, fn, video_dir=None, size=(1280, 720)):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("needs Playwright: pip install playwright && playwright install chromium")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        opts = dict(viewport={"width": size[0], "height": size[1]})
        if video_dir:
            opts.update(record_video_dir=video_dir, record_video_size=opts["viewport"])
        ctx = browser.new_context(**opts)
        page = ctx.new_page()
        page.goto(url)
        fn(page)
        video = page.video
        ctx.close()  # the video file is only complete once the context closes
        browser.close()
        return video.path() if video else None


def clip(out_dir, seconds, url="", window=""):
    """Record `seconds` -> out_dir/clip.mp4 (+ clip_sheet.jpg). Providers: REVIEW_INBOX_CLIP_CMD, a page (Playwright), or the
    screen / a window (ffmpeg). Returns the media names made."""
    mp4 = os.path.join(out_dir, "clip.mp4")
    if os.environ.get("REVIEW_INBOX_CLIP_CMD"):  # any recorder you like: a replay buffer, an engine's own capture, ...
        subprocess.run(os.environ["REVIEW_INBOX_CLIP_CMD"].format(out=mp4, seconds=seconds), shell=True)
    elif url:
        webm = playwright_page(url, lambda page: page.wait_for_timeout(seconds * 1000), video_dir=out_dir)
        if ffmpeg():
            subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", webm, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", mp4])
            os.remove(webm)
        else:
            os.replace(webm, os.path.join(out_dir, "clip.webm"))
    else:
        if not ffmpeg():
            raise SystemExit("needs ffmpeg on PATH (or set REVIEW_INBOX_CLIP_CMD)")
        if os.name == "nt":
            src = ["-f", "gdigrab", "-framerate", "30", "-i", f"title={window}" if window else "desktop"]
        elif sys.platform == "darwin":  # list devices: ffmpeg -f avfoundation -list_devices true -i ""
            src = ["-f", "avfoundation", "-framerate", "30", "-i", os.environ.get("REVIEW_INBOX_AVF_INPUT", "1:none")]
        else:
            src = ["-f", "x11grab", "-framerate", "30", "-i", os.environ.get("DISPLAY", ":0")]
        subprocess.run([ffmpeg(), "-v", "error", "-y", *src, "-t", str(seconds), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4])
    if os.path.exists(mp4) and ffmpeg():
        sheet(mp4, out_dir, seconds)
    return [m for m in ("clip.mp4", "clip.webm", "clip_sheet.jpg") if os.path.exists(os.path.join(out_dir, m))]


def draft_dir():
    d = os.path.join(DIR, "_drafts", time.strftime("%Y%m%d-%H%M%S"))  # no item.json, so never listed; nothing is posted
    os.makedirs(d)
    return d


# ---------------------------------------------------------------- telling the human

def toast(title, body):
    """Best effort desktop notification; on Windows clicking it opens the inbox page."""
    if os.environ.get("REVIEW_INBOX_NO_TOAST"):
        return
    quiet = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        if os.name == "nt":
            from xml.sax.saxutils import quoteattr, escape
            xml = (f'<toast activationType="protocol" launch={quoteattr(PAGE)}><visual><binding template="ToastGeneric">'
                   f'<text>{escape(title)}</text><text>{escape(body)}</text></binding></visual></toast>')
            ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]>$null;"
                  "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]>$null;"
                  "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;$x.LoadXml($env:TOAST_XML);"
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                  r"'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show("
                  "(New-Object Windows.UI.Notifications.ToastNotification $x))")
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             env={**os.environ, "TOAST_XML": xml}, **quiet)
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                              "-e", "end run", title, body], **quiet)
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, body], **quiet)
    except OSError:
        pass


def ensure_server():
    """Start `serve` in the background unless an inbox page already answers on PORT."""
    if os.environ.get("REVIEW_INBOX_NO_SERVER"):
        return
    import urllib.request
    try:
        got = urllib.request.urlopen(PAGE + "api/dir", timeout=1).read().decode()
        if os.path.normcase(got) != os.path.normcase(DIR):
            print(f"note: port {PORT} serves another project's inbox ({got}); set REVIEW_INBOX_PORT to give this one its own")
        return
    except OSError:
        pass
    kw = dict(creationflags=0x00000008 | 0x00000200) if os.name == "nt" else dict(start_new_session=True)  # detached
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"], env={**os.environ, "REVIEW_INBOX_DIR": DIR},
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)


# ---------------------------------------------------------------- the agent side of the loop

def cli(sid):
    """The command line to hand an agent: carries its session, so agents without CLAUDE_CODE_SESSION_ID still own their items."""
    return CLI + (f" --session {sid}" if sid else "")


def act(sid):
    return ("The human answered your review item(s) in the review inbox. This is work, not a notification: REDO = rework it and post "
            f"the next take now; OK = finish it (commit if your project's rules say so), then `{cli(sid)} handled <id> \"committed <hash>\"`.\n")


def verdict(item):
    return (f"ANSWERED {item['status'].upper()}: {item['title']} ({item['id']})" + (f"\nnote: {item['note']}" if item["note"] else "")
            + reply_paths(item))


def answer_key(item):
    return f"{item.get('answered')}|{item['status']}|{item['note']}|{item.get('reply')}"  # a re-answer is a new delivery


def undelivered(sid):
    """Answers to this session's items it hasn't been shown yet (a live `wait` delivers its own)."""
    return [i for i in mine(sid) if i.get("answered") and marked(i["id"], "delivered") != answer_key(i) and not waiting(i["id"])]


def deliver(found):
    for i in found:
        mark(i["id"], "delivered", answer_key(i))
    return "\n".join(verdict(i) for i in found)


def unresolved(sid):
    """This session's REDO/OK verdicts it hasn't acted on: no newer item posted by it since the answer, no `handled` mark."""
    own = mine(sid)
    out = []
    for i in own:
        a = i.get("answered") or ""
        if i["status"] not in ("redo", "ok") or marked(i["id"], "handled") is not None:
            continue
        if any(j["id"] > i["id"] and j.get("created", "") >= a[:16] for j in own):
            continue
        out.append(i)
    return out


def last_turn(sid):
    """Time of the last entry in the session's transcript (how long it has been idle, so whether its prompt cache is warm)."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    for p in glob.glob(os.path.join(cfg, "projects", "*", f"{sid}.jsonl")) if sid else ():
        try:
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - 262144))
                ts = re.findall(r'"timestamp":"([^"]+)"', f.read().decode("utf-8", "replace"))
            if ts:
                return datetime.fromisoformat(ts[-1].replace("Z", "+00:00")).timestamp()
        except (OSError, ValueError):
            pass
    return 0


def wait(item_id="", sid=None):
    """Block until the human answers item_id, or any of this session's open items without an id (0); revoked (4);
    nothing to wait on (5); give up while sid's prompt cache is still warm (3), so the answer never wakes a cold cache."""
    sid = SID if sid is None else sid
    start = time.time()
    ids = [os.path.basename(item_id)] if item_id else [i["id"] for i in mine(sid) if not i.get("answered")]
    if not ids:
        print("No open items posted by this session.")
        return 5
    try:
        while True:
            got = []
            for i in ids:
                try:
                    with open(os.path.join(DIR, i, "item.json"), encoding="utf-8") as f:
                        got.append(json.load(f))
                    mark(i, "waiting.json", json.dumps({"pid": os.getpid(), "sid": sid}))  # heartbeat: the page highlights it
                except FileNotFoundError:
                    pass
            if not got:
                print("Item was revoked.")
                return 4
            done = [i for i in got if i.get("answered")]  # already answered counts: the human may answer between `add` and `wait`
            if done:
                print(act(sid) + deliver(done))
                return 0
            last = last_turn(sid) or start  # no transcript to read (another agent): the agent went idle when `wait` began
            if last and time.time() - last > (WARM_MIN - 5) * 60:
                for i in got:
                    mark(i["id"], "gaveup")  # the Stop hook won't ask for a re-arm; the answer comes with the next prompt
                print("No answer yet and this session is about to go cold: stopped waiting. Don't re-arm; the verdict reaches you with your next prompt.")
                return 3
            time.sleep(BEAT_S)
    finally:
        for i in ids:
            try:
                os.remove(os.path.join(DIR, i, "waiting.json"))
            except OSError:  # gone with the folder if the item was revoked
                pass


def hook():
    """Claude Code hook. SessionStart: tells the agent the inbox exists. UserPromptSubmit: verdicts arrive with the prompt.
    Stop: the turn can't end before the agent has read a verdict, acted on it, and armed `wait` for its open items."""
    ev = json.load(sys.stdin)
    sid, name = ev.get("session_id", ""), ev.get("hook_event_name")
    if name == "SessionStart":
        print(f"This project has a review inbox ({PAGE}). When you finish something a human has to judge by eye, ear or feel "
              f"(UI, visuals, animation, motion, sound, copy), post it there instead of asking in chat: see the review-inbox skill. CLI (use exactly this, it carries your session): {cli(sid)}")
        return
    stop = name == "Stop"
    found = undelivered(sid)
    if found:
        text = act(sid) + deliver(found)
        print(json.dumps({"decision": "block", "reason": text}) if stop else text)
        return
    if stop and not ev.get("stop_hook_active"):
        todo = unresolved(sid)
        if todo:
            print(json.dumps({"decision": "block", "reason": "You haven't acted on the human's verdict yet:\n" + "\n".join(verdict(i) for i in todo)
                              + f"\nREDO: rework it and post the next take (`{cli(sid)} add`). OK: finish it, then `{cli(sid)} handled <id> "
                              "\"committed <hash>\"`. Only if you truly can't go on alone (you need the human's answer): "
                              f"`{cli(sid)} handled <id> \"<why>\"` and ask them."}))
            return
        idle = [i["id"] for i in mine(sid) if not i.get("answered") and not waiting(i["id"]) and marked(i["id"], "gaveup") is None]
        if idle:
            print(json.dumps({"decision": "block", "reason": f"Open review item(s) {', '.join(idle)} with nothing waiting for the answer: "
                              f"run `{cli(sid)} wait` as a background command (run_in_background), then end your turn."}))


# ---------------------------------------------------------------- the page

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_body(self, body, ctype="application/json", code=200):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path):
        """With `Range` support: without it every <video> pulls its whole clip just for metadata and can't seek."""
        import mimetypes
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        start, end = 0, size - 1
        if m and m.groups() != ("", ""):
            a, b = m.groups()
            start, end = (int(a), min(int(b or size - 1), size - 1)) if a else (max(size - int(b), 0), size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0 and (chunk := f.read(min(left, 1 << 16))):
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except ConnectionError:  # browsers drop media requests all the time (seek, scroll away)
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html"), "rb") as f:
                return self.send_body(f.read(), "text/html; charset=utf-8")
        if path == "/api/review":
            return self.send_body(json.dumps([{**i, "waiting": waiting(i["id"])} for i in items()]))
        if path == "/api/dir":
            return self.send_body(DIR, "text/plain; charset=utf-8")
        m = re.fullmatch(r"/media/([^/]+)/([^/]+)", path)
        if m:
            from urllib.parse import unquote
            item_id, name = unquote(m[1]), unquote(m[2])
            item = next((i for i in items() if i["id"] == item_id), None)  # only an item's own files, never a path
            if item and name in item["media"] + item.get("reply", []):
                return self.send_file(os.path.join(DIR, item_id, name))
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/review":
            return self.send_error(404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if body.get("remove"):
                return self.send_body(json.dumps(remove_reply(body.get("id", ""), body["remove"])))
            return self.send_body(json.dumps(answer(body.get("id", ""), body.get("status", ""), body.get("note", ""), body.get("images", []))))
        except (ValueError, OSError, StopIteration, binascii.Error) as e:
            return self.send_error(400, str(e))


def serve():
    os.makedirs(DIR, exist_ok=True)
    print(f"Review inbox for {DIR} on http://{HOST}:{PORT}/")
    http.server.ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # a note with a char the console code page lacks mustn't crash `list`
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="the agent session posting/waiting (default: REVIEW_INBOX_SESSION or CLAUDE_CODE_SESSION_ID)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--title", required=True)
    a.add_argument("--topic", default="")
    a.add_argument("--how", default="", help="what to look at and how to get there")
    a.add_argument("--media", nargs="*", default=[])
    a.add_argument("--by", default=os.environ.get("REVIEW_INBOX_BY", ""))
    a.add_argument("--kind", default="review", choices=("review", "bug"))
    c = sub.add_parser("clip")
    c.add_argument("seconds", type=int)
    c.add_argument("--url", default="", help="record this page with Playwright instead of the screen")
    c.add_argument("--window", default="", help="Windows: record only the window with this title")
    sub.add_parser("shot").add_argument("--url", required=True)
    l = sub.add_parser("list")
    l.add_argument("--open", action="store_true")
    l.add_argument("--topic")
    sub.add_parser("wait").add_argument("id", nargs="?", default="", help="default: every open item this session posted")
    sub.add_parser("hook")
    sub.add_parser("serve")
    sub.add_parser("revoke").add_argument("id")
    d_ = sub.add_parser("dismiss")
    d_.add_argument("id")
    d_.add_argument("note", help="what fixed it: commit, cause")
    h_ = sub.add_parser("handled")
    h_.add_argument("id")
    h_.add_argument("what", help='"committed <hash>", or why you are stopping (and ask the human)')
    args = ap.parse_args()
    SID = args.session or SID
    if args.cmd == "add":
        item, d = add(args.kind, args.title, args.topic, args.how, args.media, args.by)
        ensure_server()
        toast("Review: " + args.title, (f"{args.by}: " if args.by else "") + (args.how or args.topic))
        print(d)
        print("media:", ", ".join(item["media"]) or "none")
        print(f"Page: {PAGE}\nWake on the answer: run `{cli(SID)} wait` as a background command (run_in_background).")
    elif args.cmd == "clip":
        d = draft_dir()
        media = clip(d, args.seconds, args.url, args.window)
        if not media:
            sys.exit("no clip was made")
        print("made:", ", ".join(os.path.join(d, m) for m in media))
        if "clip_sheet.jpg" in media:
            print(f"Read {os.path.join(d, 'clip_sheet.jpg')} first; if it shows the moment, post with --media {os.path.join(d, media[0])}")
    elif args.cmd == "shot":
        d = draft_dir()
        png = os.path.join(d, "shot.png")
        playwright_page(args.url, lambda page: (page.wait_for_timeout(1000), page.screenshot(path=png)))
        print(f"Read {png} first; if it shows what you claim, post with --media {png}")
    elif args.cmd == "wait":
        sys.exit(wait(args.id))
    elif args.cmd == "hook":
        try:  # a failing hook must never block the human's prompt
            hook()
        except BaseException as e:
            sys.stderr.write(f"review hook skipped: {e!r}\n")
        sys.exit(0)
    elif args.cmd == "serve":
        serve()
    elif args.cmd == "revoke":
        revoke(args.id)
    elif args.cmd == "handled":
        mark(os.path.basename(args.id), "handled", args.what)
        print("handled:", args.what)
    elif args.cmd == "dismiss":
        print(answer(args.id, "dismissed", "fixed by an agent: " + args.note)["status"])
    else:
        for i in items():
            if (args.open and i["status"] != "open") or (args.topic and i["topic"] != args.topic):
                continue
            print(f"{i['status']:9} {i['kind']:6} {i['id']}  [{i['topic']}] {i['title']}" + (f"\n          note: {i['note']}" if i["note"] else "")
                  + reply_paths(i).replace("\n", "\n          "))
