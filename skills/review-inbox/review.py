"""Review inbox: everything an agent made that waits on a human's eyes, ears or hands, in one place.

    review.py add --title "Ball trail" --how "Open the game, watch the ball" [--topic juice] [--media a.png b.mp4]
    review.py clip 6 --url http://localhost:8080/   record a page for 6 s (Playwright) -> draft clip.mp4 + clip_sheet.jpg
    review.py clip 6 [--window "My Game"]           record the screen / a window for 6 s (ffmpeg)
    review.py clip --url ... --do "click text=Save; type hi; key Enter"   film an interaction (steps below)
    review.py shot [--url ... | --window "My Game"] still of a page (Playwright) or a window / the screen -> draft shot.png
    review.py doctor                optional capture tools: what's missing, what it disables, the install command
    review.py ask "Red or blue paddle?" [--options Red Blue]   a question for the human: answered on the page like a verdict
    review.py list [--open] [--topic juice]
    review.py wait [<id>]           (background) exits when the human answers -> wakes the agent; no id = every open item
                                    this session posted. Gives up (exit 3) before the session's prompt cache goes cold.
    review.py handled <id> "<what>" the verdict is dealt with without a new item ("committed abc123", or why you stopped)
    review.py revoke <id>           take your own unanswered item down (e.g. before posting a retake)
    review.py dismiss <id> "<note>" close an open item that no longer needs an answer (superseded, already fixed)
    review.py serve                 the review page (also started on demand by `add`)
    review.py hook                  Claude Code hook (SessionStart, UserPromptSubmit, Stop): hands a session its verdicts

Storage: one inbox per user, ~/.review-inbox/<id>/ (item.json + media): every project's agents post to the same page, and
each item names its project. One folder per item, so parallel sessions never write the same file. Status: open -> ok | redo | dismissed, or open -> answered for a question (`ask`).
Items remember the posting session (CLAUDE_CODE_SESSION_ID in Claude Code; --session or REVIEW_INBOX_SESSION elsewhere,
which the SessionStart hook hands the agent); the hook hands that session its verdicts, with the next
prompt or by blocking the end of a turn until it has read and acted on them.

Environment: REVIEW_INBOX_DIR (default ~/.review-inbox; --dir overrides it), REVIEW_INBOX_PORT (7700),
REVIEW_INBOX_HOST (127.0.0.1), REVIEW_INBOX_SESSION, REVIEW_INBOX_BY, REVIEW_INBOX_AVF_INPUT (macOS capture device),
REVIEW_INBOX_BEAT_S (5: how often `wait` polls),
Steps (--do "a; b; c", or --script <file> with one per line): click <target>, dblclick <target>, move <target>,
type <text>, key <combo>, scroll <dy>, wait <ms>. A target is a Playwright selector on a page (text=Save, #email) or x,y:
on a page in viewport pixels, on the desktop relative to the --window's top-left (pick them from `shot --window`), else
the screen. Desktop steps need pyautogui; the cursor glides to each target so the viewer sees what was clicked.

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
STATUSES = ("open", "ok", "redo", "dismissed", "answered")
ANSWERS = {"review": ("ok", "redo", "dismissed"), "question": ("answered", "dismissed")}  # what the human can set, per kind
MAX_BODY = 32 << 20  # a POST from the page: a note plus a few pasted screenshots
IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def inbox_dir():
    """One inbox per user, the same for the hooks and the CLI wherever they run. REVIEW_INBOX_DIR moves it (a project
    can keep its own by pointing this at <project>/.review-inbox, with its own REVIEW_INBOX_PORT)."""
    return os.path.abspath(os.environ.get("REVIEW_INBOX_DIR") or os.path.join(os.path.expanduser("~"), ".review-inbox"))


def project_dir():
    """The project an item comes from, only to label it: the agent's project, else the git root, else the cwd."""
    root = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not root:
        try:
            root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return os.path.normpath(root or os.getcwd())


DIR = inbox_dir()


# ---------------------------------------------------------------- store

def add(kind, title, topic="", how="", media=(), by="", options=None):
    missing = [m for m in media if not os.path.isfile(m)]
    if missing:  # before the folder exists, so a typo leaves nothing behind
        raise SystemExit("no such file: " + ", ".join(missing))
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    base = os.path.join(DIR, time.strftime("%Y%m%d-%H%M%S-") + slug)
    d, n = base, 1
    while True:  # two posts with one title in the same second
        try:
            os.makedirs(d)
            break
        except FileExistsError:
            n += 1
            d = f"{base}-{n}"
    if not os.path.exists(os.path.join(DIR, ".gitignore")):  # the inbox never ends up in the project's commits
        with open(os.path.join(DIR, ".gitignore"), "w") as f:
            print("*", file=f)
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
    proj = project_dir()
    item = dict(id=os.path.basename(d), kind=kind, title=title, topic=topic, how=how, by=by,
                sid=SID,  # the poster: `hook` hands the verdict to this session
                project=os.path.basename(proj) or proj, project_dir=proj,
                media=names, created=time.strftime("%Y-%m-%d %H:%M"), status="open", note="")
    if options is not None:  # a question's one-click answers, written with the item so the page never sees it without them
        item["options"] = options
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
    if item_id not in os.listdir(DIR):  # id comes from the page: never a path
        raise ValueError("bad id")
    item = next(i for i in items() if i["id"] == item_id)
    if status not in ANSWERS.get(item.get("kind"), ()):
        raise ValueError(f"a {item.get('kind')} can't be set to {status!r}")
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
    if item.get("sid") and item["sid"] != SID:
        raise SystemExit("posted by another session, left in place")
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


STEP_VERBS = ("click", "dblclick", "move", "type", "key", "scroll", "wait")
LEAD_MS = 500  # before the first step and after the last, so the moment isn't cut off


def parse_steps(text, sep=";"):
    """'click text=Save; type hi' -> [("click", "text=Save"), ("type", "hi")]. --script files split on lines only, so a
    typed ';' survives there."""
    steps = []
    for raw in text.split(sep):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        verb, _, arg = raw.partition(" ")
        if verb not in STEP_VERBS:
            raise SystemExit(f"unknown step {verb!r} in {raw!r}; steps: {', '.join(STEP_VERBS)}")
        if verb != "type":
            arg = arg.strip()
        if not arg:
            raise SystemExit(f"step {raw!r} needs an argument")
        steps.append((verb, arg))
    return steps


def xy(target):
    m = re.fullmatch(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*", target)
    return (int(m[1]), int(m[2])) if m else None


# headless Chromium draws no cursor: a dot that follows the mouse and pulses on click
CURSOR_JS = """addEventListener('DOMContentLoaded', () => {
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;width:22px;height:22px;margin:-11px 0 0 -11px;' +
    'border-radius:50%;background:rgba(255,255,255,.55);border:2px solid #111;box-shadow:0 0 0 2px #fff;left:-50px;top:-50px;transition:transform .12s';
  document.documentElement.appendChild(d);
  addEventListener('mousemove', e => { d.style.left = e.clientX + 'px'; d.style.top = e.clientY + 'px'; }, true);
  addEventListener('mousedown', () => d.style.transform = 'scale(.6)', true);
  addEventListener('mouseup', () => d.style.transform = '', true);
});"""


def web_steps(page, steps):
    def at(target):
        if xy(target):
            return xy(target)
        try:
            box = page.locator(target).first.bounding_box(timeout=5000)
        except Exception:  # Playwright's TimeoutError: say which step, not 30 lines of stack
            box = None
        if not box:
            raise SystemExit(f"nothing visible matches {target!r}")
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    for verb, arg in steps:
        if verb in ("click", "dblclick", "move"):
            page.mouse.move(*at(arg), steps=20)
            page.wait_for_timeout(150)
            if verb == "click":
                page.mouse.click(*at(arg))
            elif verb == "dblclick":
                page.mouse.dblclick(*at(arg))
        elif verb == "type":
            page.keyboard.type(arg, delay=60)
        elif verb == "key":
            page.keyboard.press(arg)
        elif verb == "scroll":
            page.mouse.wheel(0, int(arg))
        elif verb == "wait":
            page.wait_for_timeout(int(arg))
        page.wait_for_timeout(250)


def win_origin(window):
    """Screen position of a window's client area (what gdigrab records), brought to the front. (0, 0) without a window."""
    if not window:
        return 0, 0
    if os.name != "nt":
        raise SystemExit("--window works on Windows; elsewhere steps use screen coordinates")
    import ctypes, ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels, like ffmpeg
    except (AttributeError, OSError):
        pass
    h = user32.FindWindowW(None, window)
    if not h:
        raise SystemExit(f"no window titled exactly {window!r}")
    user32.ShowWindow(h, 9)  # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)  # a tap of Alt lets SetForegroundWindow through Windows' focus-steal guard
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(h)
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(h, ctypes.byref(pt))
    return pt.x, pt.y


def desktop_steps(steps, origin):
    try:
        import pyautogui
    except ImportError:
        raise SystemExit("desktop steps need pyautogui: pip install pyautogui")
    pyautogui.PAUSE = 0.25
    for verb, arg in steps:
        if verb in ("click", "dblclick", "move"):
            if not xy(arg):
                raise SystemExit(f"desktop targets are x,y (from `shot --window`), not {arg!r}")
            x, y = xy(arg)
            pyautogui.moveTo(origin[0] + x, origin[1] + y, duration=0.5, tween=pyautogui.easeInOutQuad)
            if verb == "click":
                pyautogui.click()
            elif verb == "dblclick":
                pyautogui.doubleClick()
        elif verb == "type":
            pyautogui.write(arg, interval=0.06)
        elif verb == "key":
            pyautogui.hotkey(*[k.strip().lower() for k in arg.split("+")])
        elif verb == "scroll":
            pyautogui.scroll(-int(arg))  # positive dy scrolls down, as on the web
        elif verb == "wait":
            time.sleep(int(arg) / 1000)


def screen_src(window):
    if os.name == "nt":
        return ["-f", "gdigrab", "-framerate", "30", "-i", f"title={window}" if window else "desktop"]
    if sys.platform == "darwin":  # list devices: ffmpeg -f avfoundation -list_devices true -i ""
        return ["-f", "avfoundation", "-framerate", "30", "-i", os.environ.get("REVIEW_INBOX_AVF_INPUT", "1:none")]
    return ["-f", "x11grab", "-framerate", "30", "-i", os.environ.get("DISPLAY", ":0")]


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
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        page.goto(url)
        fn(page)
        video = page.video
        ctx.close()  # the video file is only complete once the context closes
        browser.close()
        return video.path() if video else None


def clip(out_dir, seconds, url="", window="", steps=()):
    """Record `seconds` (with steps: until they're done, and at least `seconds`) -> out_dir/clip.mp4 (+ clip_sheet.jpg).
    Providers: REVIEW_INBOX_CLIP_CMD, a page (Playwright), or the screen / a window (ffmpeg). Returns the media names made."""
    mp4 = os.path.join(out_dir, "clip.mp4")
    if window and os.name != "nt" and not url:
        raise SystemExit("--window works on Windows only; leave it out to record the whole screen")
    if os.environ.get("REVIEW_INBOX_CLIP_CMD") and not steps:  # any recorder you like: a replay buffer, an engine's own capture
        subprocess.run(os.environ["REVIEW_INBOX_CLIP_CMD"].format(out=mp4, seconds=seconds), shell=True)
    elif url:
        def film(page):
            t = time.time()
            page.wait_for_timeout(LEAD_MS)
            web_steps(page, steps)
            page.wait_for_timeout(max(LEAD_MS, seconds * 1000 - (time.time() - t) * 1000))
        webm = playwright_page(url, film, video_dir=out_dir)
        ok = ffmpeg() and subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", webm, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                          "-movflags", "+faststart", mp4]).returncode == 0
        if ok:
            os.remove(webm)
        else:  # no ffmpeg, or it failed: keep the recording as it is
            os.replace(webm, os.path.join(out_dir, "clip.webm"))
    else:
        if not ffmpeg():
            raise SystemExit("needs ffmpeg on PATH (or set REVIEW_INBOX_CLIP_CMD)")
        enc = ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4]
        if not steps:
            subprocess.run([ffmpeg(), "-v", "error", "-y", *screen_src(window), "-t", str(seconds), *enc])
        else:
            origin = win_origin(window)  # focus first, so the recording opens on the window in front
            time.sleep(0.3)
            rec = subprocess.Popen([ffmpeg(), "-v", "error", "-y", *screen_src(window), "-t", "300", *enc], stdin=subprocess.PIPE)
            t = time.time()
            try:
                time.sleep(0.4 + LEAD_MS / 1000)  # ffmpeg takes a moment to start grabbing
                desktop_steps(steps, origin)
                time.sleep(max(LEAD_MS / 1000, seconds - (time.time() - t)))
            finally:
                try:
                    rec.communicate(b"q", timeout=20)  # 'q' lets ffmpeg finish the mp4; a kill leaves it unplayable
                except subprocess.TimeoutExpired:
                    rec.kill()
    if os.path.exists(mp4) and ffmpeg():
        sheet(mp4, out_dir, duration(mp4) or seconds)
    return [m for m in ("clip.mp4", "clip.webm", "clip_sheet.jpg") if os.path.exists(os.path.join(out_dir, m))]


def duration(mp4):
    r = subprocess.run([ffmpeg(), "-i", mp4], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0


def shot(png, url="", window="", steps=()):
    if window and os.name != "nt" and not url:
        raise SystemExit("--window works on Windows only; leave it out for the whole screen")
    if url:
        return playwright_page(url, lambda page: (page.wait_for_timeout(1000), web_steps(page, steps),
                                                  page.wait_for_timeout(300), page.screenshot(path=png)))
    if not ffmpeg():
        raise SystemExit("needs ffmpeg on PATH (see `review.py doctor`)")
    if steps:
        desktop_steps(steps, win_origin(window))
        time.sleep(0.3)
    elif window:
        win_origin(window)
        time.sleep(0.3)
    subprocess.run([ffmpeg(), "-v", "error", "-y", *screen_src(window), "-frames:v", "1", "-update", "1", png])


# ---------------------------------------------------------------- doctor

def doctor():
    """Optional capture tools: [(what, disables, fix)] for each one missing. Fast (no imports, no browser launch): it runs at
    every session start."""
    import importlib.util
    has = lambda mod: importlib.util.find_spec(mod) is not None
    mac, linux = sys.platform == "darwin", sys.platform.startswith("linux")
    pip = f'"{sys.executable}" -m pip install'
    out = []
    if sys.version_info < (3, 9):
        out.append(("Python 3.9+", "everything", "install a newer Python"))
    if not ffmpeg():
        out.append(("ffmpeg", "screen and window recording, contact sheets, mp4 from page clips",
                    "brew install ffmpeg" if mac else "sudo apt install ffmpeg" if linux else "winget install Gyan.FFmpeg"))
    if not has("playwright"):
        out.append(("playwright", "clips and stills of web pages", f"{pip} playwright && playwright install chromium"))
    else:
        home = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser(
            "~/Library/Caches/ms-playwright" if mac else "~/.cache/ms-playwright" if linux
            else os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"))
        if not glob.glob(os.path.join(home, "chromium*")):
            out.append(("Playwright's Chromium", "clips and stills of web pages", f'"{sys.executable}" -m playwright install chromium'))
    if not has("pyautogui"):
        out.append(("pyautogui", "scripted steps (--do) on desktop apps", f"{pip} pyautogui"))
    if mac:
        try:
            import ctypes, ctypes.util
            cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
            if not cg.CGPreflightScreenCaptureAccess():
                out.append(("Screen Recording permission", "screen and window recording",
                            "System Settings > Privacy & Security > Screen Recording: allow your terminal / agent app"))
            ax = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
            if not ax.AXIsProcessTrusted():
                out.append(("Accessibility permission", "scripted steps (--do) on desktop apps",
                            "System Settings > Privacy & Security > Accessibility: allow your terminal / agent app"))
        except (OSError, AttributeError):
            pass
    if linux and (os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY")):
        out.append(("an X11 session", "screen recording and desktop steps (x11grab and pyautogui need X11; Wayland is not supported)",
                    "log in to an X11 / Xorg session, or film web pages with --url"))
    return out


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
            print(f"note: port {PORT} serves another inbox ({got}), not {DIR}; give one of them its own REVIEW_INBOX_PORT")
        return
    except OSError:
        pass
    kw = dict(creationflags=0x00000008 | 0x00000200) if os.name == "nt" else dict(start_new_session=True)  # detached
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"], env={**os.environ, "REVIEW_INBOX_DIR": DIR},
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)


# ---------------------------------------------------------------- the agent side of the loop

def cli(sid):
    """The command line to hand an agent: carries its session, so agents without CLAUDE_CODE_SESSION_ID still own their items."""
    return CLI + f' --dir "{DIR}"' + (f" --session {sid}" if sid else "")  # the CLI finds the same inbox as the hooks


def act(sid):
    return ("The human answered in the review inbox. This is work, not a notification: REDO = rework it and post the next take now; "
            f"OK = finish it (commit if your project's rules say so), then `{cli(sid)} handled <id> \"committed <hash>\"`; "
            "an answer to your question = carry on with it.\n")


def verdict(item):
    if item.get("kind") == "question":
        return f"ANSWER to your question \"{item['title']}\" ({item['id']}): {item['note'] or '(no text)'}" + reply_paths(item)
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
        print(f"You have a review inbox ({PAGE}). When you finish something a human has to judge by eye, ear or feel "
              f"(UI, visuals, animation, motion, sound, copy), post it there instead of asking in chat: see the review-inbox skill. CLI (use exactly this, it carries your session): {cli(sid)}"
              + (" (in PowerShell, put `& ` in front)" if os.name == "nt" else ""))
        try:  # only in a project that already uses the inbox: every other session hears nothing about capture tools
            missing = doctor() if items() else []
        except Exception:
            missing = []
        if missing:
            print("Review inbox capture tools missing (offer to install them): " + "; ".join(
                f"{w} (disables {d}): {f}" for w, d, f in missing) + f". Details: {cli(sid)} doctor")
        return
    stop = name == "Stop"
    if name == "UserPromptSubmit":  # a reply in the chat overtakes this session's open questions; reviews stay open
        for i in mine(sid):
            if i.get("kind") == "question" and not i.get("answered"):
                done = answer(i["id"], "answered", "answered in the chat: " + (ev.get("prompt") or "")[:300])
                mark(i["id"], "delivered", answer_key(done))  # it isn't news to the agent: don't hand it back as a verdict
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
                              f"`{cli(sid)} handled <id> \"<why>\"`, then post your question with `{cli(sid)} ask` and run `wait`."}))
            return
        idle = [i["id"] for i in mine(sid) if not i.get("answered") and not waiting(i["id"]) and marked(i["id"], "gaveup") is None]
        if idle:
            print(json.dumps({"decision": "block", "reason": f"Open review item(s) {', '.join(idle)} with nothing waiting for the answer: "
                              f"run `{cli(sid)} wait` as a background command (run_in_background), then end your turn."}))
            return
        # the human steers through the inbox and misses questions asked in chat: catch a turn that ends on one, once.
        # Only in a session that has used the inbox; anywhere else a question in chat is just a question.
        own = mine(sid)
        if own and not any(not i.get("answered") for i in own) and asks_human(last_message(ev)):
            print(json.dumps({"decision": "block", "reason": f"If that's a question for the human, post it with `{cli(sid)} ask \"<question>\" "
                              "[--options A B ...]` and run `wait` as a background command; otherwise just end your turn."}))


def last_message(ev):
    """The agent's last reply: from the hook input (Codex) or the tail of the transcript (Claude Code). '' when unknown."""
    if ev.get("last_assistant_message"):
        return ev["last_assistant_message"]
    try:
        with open(ev.get("transcript_path") or "", "rb") as f:
            f.seek(max(0, os.path.getsize(f.name) - 262144))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "assistant":
            content = (e.get("message") or {}).get("content")
            text = content if isinstance(content, str) else "".join(c.get("text", "") for c in content or [] if isinstance(c, dict))
            if text.strip():
                return text
    return ""


def asks_human(text):
    """Does a reply end on a question to the human: its last paragraph ends with '?', or asks one while offering choices."""
    tail = text.strip().split("\n\n")[-1].strip()
    if tail.rstrip(" *_`)\"'").endswith("?"):
        return True
    return "?" in tail and re.search(r"(?i)\b(option|which|would you like|do you want|should i|shall i|prefer|or)\b", tail) is not None


# ---------------------------------------------------------------- the page

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def local(self):
        """Only this page may talk to the server: a Host other than ours is DNS rebinding, a foreign Origin is another site."""
        host, origin = self.headers.get("Host", ""), self.headers.get("Origin")
        loopback = HOST in ("127.0.0.1", "localhost", "::1")  # bound wider (REVIEW_INBOX_HOST), any address of ours is fine
        if (loopback and host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")) or (origin and origin != f"http://{host}"):
            self.send_error(403)
            return False
        return True

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
        self.send_header("X-Content-Type-Options", "nosniff")
        if not ctype.startswith(("image/", "video/", "audio/")) or ctype == "image/svg+xml":  # an attached .html/.svg can't run here
            self.send_header("Content-Disposition", "attachment")
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
        if not self.local():
            return
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
        if not self.local():
            return
        if self.path != "/api/review":
            return self.send_error(404)
        if not self.headers.get("Content-Type", "").startswith("application/json"):  # a form post from another site can't set it
            return self.send_error(415)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size > MAX_BODY:
                return self.send_error(413)
            body = json.loads(self.rfile.read(size) or b"{}")
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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # the human's note is the payload: never the console code page
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="the agent session posting/waiting (default: REVIEW_INBOX_SESSION or CLAUDE_CODE_SESSION_ID)")
    ap.add_argument("--dir", help="the inbox folder (default: REVIEW_INBOX_DIR, else <project>/.review-inbox)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--title", required=True)
    a.add_argument("--topic", default="")
    a.add_argument("--how", default="", help="what to look at and how to get there")
    a.add_argument("--media", nargs="*", default=[])
    a.add_argument("--by", default=os.environ.get("REVIEW_INBOX_BY", ""))
    c = sub.add_parser("clip")
    c.add_argument("seconds", type=int, nargs="?", default=0, help="length; with steps, the minimum")
    sh = sub.add_parser("shot")
    for x in (c, sh):
        x.add_argument("--url", default="", help="a page (Playwright) instead of the screen")
        x.add_argument("--window", default="", help="Windows: only the window with exactly this title")
        x.add_argument("--do", default="", help='steps, e.g. "click text=Save; type hello; key Enter"')
        x.add_argument("--script", default="", help="a file of steps, one per line")
    sub.add_parser("doctor")
    q = sub.add_parser("ask")
    q.add_argument("question")
    q.add_argument("--options", nargs="*", default=[], help="one-click answers shown on the page")
    q.add_argument("--topic", default="")
    q.add_argument("--by", default=os.environ.get("REVIEW_INBOX_BY", ""))
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
    DIR = os.path.abspath(args.dir) if args.dir else DIR
    if args.cmd in ("handled", "revoke", "dismiss") and not os.path.isfile(os.path.join(DIR, os.path.basename(args.id), "item.json")):
        sys.exit(f"no item {args.id!r} in {DIR}")
    steps = [] if args.cmd not in ("clip", "shot") else (
        parse_steps(args.do) + (parse_steps(open(args.script, encoding="utf-8").read(), "\n") if args.script else []))
    if args.cmd == "add":
        item, d = add("review", args.title, args.topic, args.how, args.media, args.by)
        ensure_server()
        toast("Review: " + args.title, (f"{args.by}: " if args.by else "") + (args.how or args.topic))
        print(d)
        print("media:", ", ".join(item["media"]) or "none")
        print(f"Page: {PAGE}\nWake on the answer: run `{cli(SID)} wait` as a background command (run_in_background).")
    elif args.cmd == "clip":
        if not args.seconds and not steps:
            sys.exit("give a length in seconds, or steps (--do / --script)")
        d = draft_dir()
        media = clip(d, args.seconds, args.url, args.window, steps)
        if not media:
            sys.exit(f"no clip was made; `{cli(SID)} doctor` lists missing capture tools")
        print("made:", ", ".join(os.path.join(d, m) for m in media))
        if "clip_sheet.jpg" in media:
            print(f"Read {os.path.join(d, 'clip_sheet.jpg')} first; if it shows the moment, post with --media {os.path.join(d, media[0])}")
    elif args.cmd == "ask":
        item, d = add("question", args.question, args.topic, by=args.by, options=args.options)
        ensure_server()
        toast("Question: " + args.question, (f"{args.by}: " if args.by else "") + " / ".join(args.options))
        print(d)
        print(f"Page: {PAGE}\nWake on the answer: run `{cli(SID)} wait` as a background command (run_in_background).")
    elif args.cmd == "shot":
        d = draft_dir()
        png = os.path.join(d, "shot.png")
        shot(png, args.url, args.window, steps)
        if not os.path.exists(png):
            sys.exit("no still was made")
        print(f"Read {png} first; if it shows what you claim, post with --media {png}")
        if args.window:
            print("Desktop step targets are x,y pixels in this image (from its top-left).")
    elif args.cmd == "doctor":
        missing = doctor()
        for w, d, f in missing:
            print(f"missing  {w}\n         disables: {d}\n         install:  {f}")
        print("all capture tools present" if not missing else f"{len(missing)} missing; the rest of the inbox works without them")
        sys.exit(1 if missing else 0)
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
            print(f"{i['status']:9} {i['kind']:6} {i['id']}  {i.get('project', '')}{'/' + i['topic'] if i['topic'] else ''}: {i['title']}" + (f"\n          note: {i['note']}" if i["note"] else "")
                  + reply_paths(i).replace("\n", "\n          "))
