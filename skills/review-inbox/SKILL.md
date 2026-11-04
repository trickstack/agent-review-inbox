---
name: review-inbox
description: Post work a human has to judge by eye, ear or feel (UI and layout changes, visuals, animation, game feel, effects, sound, copy) to the project's review inbox with a clip or still, wait for their OK or Redo, and act on the verdict. Also where questions for the human go. Use whenever you finish such a change or need the human to decide something, instead of asking in chat or writing "needs a human check" in a notes file, and whenever a verdict or answer from the inbox reaches you.
---

# Review inbox

The human reviews your visual and feel work on one page (`http://127.0.0.1:7700/` by default), shared by every session and
project (each item is labelled with its project), and answers
**OK** or **Redo** with a note and sometimes images. Every command below is `python review.py ...`, with `review.py` in this
skill's directory. The SessionStart hook prints the full command line: use exactly that one, since it can carry
`--session <id>`, which ties your items to you in agents that don't set `CLAUDE_CODE_SESSION_ID` (Codex, others).

## The loop

1. **Capture.** Show it, don't describe it: anything that moves or makes sound gets a video. Render it to a file; don't
   film the human's screen, they may be watching something on it.
   - A web page or browser game: `review.py clip 6 --url http://localhost:8080/` (headless) or `review.py shot --url ...` for a still.
   - A game engine or native app: its own recorder, which the project sets as `REVIEW_INBOX_CLIP_CMD`; `clip` runs it.
   - Anything you rendered yourself (an offline render, a still from your own tool): pass it to `--media`.
   - An interaction (a button press, typing, a hover): add steps and `clip` films them; see below.
   - Recording the real screen or a window (`clip --window "<title>"`, `clip` with no `--url`) only works when the human
     has set `REVIEW_INBOX_DESKTOP=1`; otherwise it refuses. Only one recording runs at a time: a second one waits.
2. **Inspect before you post, every time.** Read the contact sheet (`clip_sheet.jpg`) or the still, and check it really shows
   what you are about to claim: the moment is in frame, the right thing changed, nothing is broken, it isn't black. If not,
   retake it. The human should only ever see footage you have already checked.
   - The sheet is 3 frames a second, so anything shorter than ~0.3 s (a flash, a hit, a pop) can fall between its frames.
     For those, `review.py frames <clip.mp4> --at <seconds>` tiles every frame of the 0.6 s around that moment
     (`--span`, `--fps` to change it). Read that instead.
3. **Post.** `review.py add --title "<what it is>" --topic <area> --how "<how to get there and what to judge>" --media <files>`
   - Write `--how` in calm, plain prose: where to look, what changed, what you want judged. Claim only what the footage shows.
   - Several takes go in one `add` (`--media a/clip.mp4 b/clip.mp4`), each path once, each named for what it is
     (rename `clip.mp4` to `bigger-particles.mp4` first). Put your proposal first and comparisons last.
   - Label every clip and still on the picture itself, in a corner: what it shows and, once there is more than one,
     whether it is the change or a comparison (`AFTER: bow climb, first person`, `BEFORE: old climb, for comparison`).
     The human sees the footage before the file names and must never mistake an old take for your fix. If your recorder
     can't draw text, burn it in: `ffmpeg -i in.mp4 -vf "drawtext=text='AFTER\: bow climb':x=12:y=12:fontsize=h/18:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8" -c:a copy out.mp4`.
     Skip a BEFORE take unless the change is subtle enough to need one.
   - Show only what is new. Don't re-post variants the human already decided on.
   - `add` starts the page if it isn't running and pops a desktop notification, so you don't need to alert the human.
4. **Wait.** Run `review.py wait` as a **background** command (`run_in_background`) and end your turn or keep working.
   It exits with the verdict when the human answers. It exits 3 after about 45 idle minutes (sized for a one-hour prompt cache): then don't re-arm it.
   The verdict reaches you with the next prompt instead.
   - No `run_in_background` in your agent? Start it detached and read its output later: `nohup <cli> wait > wait.log 2>&1 &`
     in bash, or `Start-Process -NoNewWindow -RedirectStandardOutput wait.log <python> "<review.py> ... wait"` in
     PowerShell. Never run it in the foreground: it can block for most of an hour.
5. **Act on the verdict. It is work, not a notification**, even when it arrives as a background-task notice.
   - **REDO**: read the note and any attached images (`list` and `wait` print their paths), rework it, and post the next take
     in the same turn. The retake is a **new item** (`add`): the REDO'd item stays as history, and your newer item is what
     satisfies the Stop hook.
   - **OK**: finish it (commit if your project's rules allow), then `review.py handled <id> "committed <hash>"`.
   - **OK with a note** (or images): the take is approved and the note is more work. Commit the approved state first, then
     do what the note asks before moving on: rework and post a new take, or start the work it asks for. Then
     `review.py handled <id> --did "<what you did about the note>"`. A commit alone doesn't close it.
   - Truly need the human's answer before you can go on? `review.py handled <id> "<why>"`, then ask (below).

The Stop hook keeps this honest. It blocks the end of your turn while a verdict on your item is unread, while a REDO or OK
has neither a newer item from you nor a `handled` mark (for an OK with a note, `handled --did`), or while you have an open item and no `wait` running.

## Filming an interaction

Give `clip` (or `shot`) steps with `--do "a; b; c"`, or `--script <file>` with one step per line. The recording runs
until the steps are done (plus a short lead-in and tail), or at least the seconds you give.
Steps: `click <target>`, `dblclick <target>`, `move <target>`, `type <text>`, `key <combo>`, `scroll <dy>`, `wait <ms>`.

- **Web page:** targets are Playwright selectors. A cursor dot is drawn so the viewer sees what was clicked.
  `review.py clip --url http://localhost:8080/ --do "click text=Settings; click #volume; key ArrowRight; wait 500"`
- **Desktop app** (only with `REVIEW_INBOX_DESKTOP=1`): targets are `x,y` pixels. Take a still first, Read it and pick the coordinates from it:
  `review.py shot --window "My App"` and then
  `review.py clip --window "My App" --do "click 320,180; type hello; click 40,386; wait 800"`.
  Coordinates are relative to the window's top-left as in that still (Windows; elsewhere use the whole screen's).
  The real mouse and keyboard move, so the human's machine is busy for those seconds.

If a tool is missing, the SessionStart hook says so with the install command. `review.py doctor` lists what's missing,
what it disables and how to install it; offer the human the install.

## Asking the human

The human steers through the inbox and may never see a question you ask in chat; your session then sits idle until its
cache goes cold. So put every question for them in the inbox, not just ones about how something looks: a go-ahead, a
choice of approach, "build X now or wait for Y?". An app's closing next-step box (a wrap_up `ask` with buttons) counts as
chat too: once you have used the inbox, keep that box to a recap and ask here:

`review.py ask "Should the trail match the ball colour or stay cyan?" --options "Match the ball" "Stay cyan"`

Options become one-click buttons (keep them short), and the human can always type an answer instead. Then run
`review.py wait` in the background like for a review. The answer comes back like a verdict: carry on with it. The Stop hook
pushes back once if your turn ends on a question with nothing posted.

## Other commands

- `review.py list [--open] [--topic <area>]`: what's open, and verdicts with their notes.
- `review.py revoke <id>`: take down your own unanswered item (posted the wrong take). Then post the right one.
- `review.py dismiss <id> "<why>"`: close an open item that no longer needs an answer (superseded, already fixed).

The inbox lives in `~/.review-inbox`, outside your project. Answered items lose their media after 7 days
(`REVIEW_INBOX_KEEP_DAYS`), so attach nothing you need to keep. If a project keeps its own (`REVIEW_INBOX_DIR` pointing inside
it), that folder git-ignores itself, so it never lands in a commit.
