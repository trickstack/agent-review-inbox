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

1. **Capture.** Show it, don't describe it: anything that moves or makes sound gets a video.
   - A web page or browser game: `review.py clip 6 --url http://localhost:8080/` (Playwright) or `review.py shot --url ...` for a still.
   - A desktop window or game: `review.py clip 6 --window "<window title>"` (Windows) or `review.py clip 6` (whole screen).
   - Any other recorder (an engine's capture, a replay buffer): the project sets `REVIEW_INBOX_CLIP_CMD`, and `clip` runs it.
   - Your own renders or screenshots work too: anything you can pass to `--media`.
   - An interaction (a button press, typing, a hover): add steps and `clip` films them; see below.
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
   - Show only what is new. Don't re-post variants the human already decided on.
   - `add` starts the page if it isn't running and pops a desktop notification, so you don't need to alert the human.
4. **Wait.** Run `review.py wait` as a **background** command (`run_in_background`) and end your turn or keep working.
   It exits with the verdict when the human answers. It exits 3 before your prompt cache would go cold: then don't re-arm it.
   The verdict reaches you with the next prompt instead.
   - No `run_in_background` in your agent? Start it detached and read its output later: `nohup <cli> wait > wait.log 2>&1 &`
     in bash, or `Start-Process -NoNewWindow -RedirectStandardOutput wait.log <python> "<review.py> ... wait"` in
     PowerShell. Never run it in the foreground: it can block for most of an hour.
5. **Act on the verdict. It is work, not a notification**, even when it arrives as a background-task notice.
   - **REDO**: read the note and any attached images (`list` and `wait` print their paths), rework it, and post the next take
     in the same turn.
   - **OK**: finish it (commit if your project's rules allow), then `review.py handled <id> "committed <hash>"`.
   - Truly need the human's answer before you can go on? `review.py handled <id> "<why>"`, then ask (below).

The Stop hook keeps this honest. It blocks the end of your turn while a verdict on your item is unread, while a REDO or OK
has neither a newer item from you nor a `handled` mark, or while you have an open item and no `wait` running.

## Filming an interaction

Give `clip` (or `shot`) steps with `--do "a; b; c"`, or `--script <file>` with one step per line. The recording runs
until the steps are done (plus a short lead-in and tail), or at least the seconds you give.
Steps: `click <target>`, `dblclick <target>`, `move <target>`, `type <text>`, `key <combo>`, `scroll <dy>`, `wait <ms>`.

- **Web page:** targets are Playwright selectors. A cursor dot is drawn so the viewer sees what was clicked.
  `review.py clip --url http://localhost:8080/ --do "click text=Settings; click #volume; key ArrowRight; wait 500"`
- **Desktop app:** targets are `x,y` pixels. Take a still first, Read it and pick the coordinates from it:
  `review.py shot --window "My App"` and then
  `review.py clip --window "My App" --do "click 320,180; type hello; click 40,386; wait 800"`.
  Coordinates are relative to the window's top-left as in that still (Windows; elsewhere use the whole screen's).
  The real mouse and keyboard move, so the human's machine is busy for those seconds.

If a tool is missing, the SessionStart hook says so with the install command. `review.py doctor` lists what's missing,
what it disables and how to install it; offer the human the install.

## Asking the human

The human steers through the inbox and may never see a question you ask in chat; your session then sits idle until its
cache goes cold. So put every question for them in the inbox:

`review.py ask "Should the trail match the ball colour or stay cyan?" --options "Match the ball" "Stay cyan"`

Options become one-click buttons (keep them short), and the human can always type an answer instead. Then run
`review.py wait` in the background like for a review. The answer comes back like a verdict: carry on with it. The Stop hook
pushes back once if your turn ends on a question with nothing posted.

## Other commands

- `review.py list [--open] [--topic <area>]`: what's open, and verdicts with their notes.
- `review.py revoke <id>`: take down your own unanswered item (posted the wrong take). Then post the right one.
- `review.py dismiss <id> "<why>"`: close an open item that no longer needs an answer (superseded, already fixed).

The inbox lives in `~/.review-inbox`, outside your project. If a project keeps its own (`REVIEW_INBOX_DIR` pointing inside
it), that folder git-ignores itself, so it never lands in a commit.
