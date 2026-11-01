---
name: review-inbox
description: Post work a human has to judge by eye, ear or feel (UI and layout changes, visuals, animation, game feel, effects, sound, copy) to the project's review inbox with a clip or still, wait for their OK or Redo, and act on the verdict. Use whenever you finish such a change, instead of asking "how does it look?" in chat or writing "needs a human check" in a notes file, and whenever a verdict from the inbox reaches you.
---

# Review inbox

The human reviews your visual and feel work on one page (`http://127.0.0.1:7700/` by default), from any session, and answers
**OK** or **Redo** with a note and sometimes images. Every command below is `python review.py ...`, with `review.py` in this
skill's directory. The SessionStart hook prints the full command line.

## The loop

1. **Capture.** Show it, don't describe it: anything that moves or makes sound gets a video.
   - A web page or browser game: `review.py clip 6 --url http://localhost:8080/` (Playwright) or `review.py shot --url ...` for a still.
   - A desktop window or game: `review.py clip 6 --window "<window title>"` (Windows) or `review.py clip 6` (whole screen).
   - Any other recorder (an engine's capture, a replay buffer): the project sets `REVIEW_INBOX_CLIP_CMD`, and `clip` runs it.
   - Your own renders or screenshots work too: anything you can pass to `--media`.
2. **Inspect before you post, every time.** Read the contact sheet (`clip_sheet.jpg`) or the still, and check it really shows
   what you are about to claim: the moment is in frame, the right thing changed, nothing is broken, it isn't black. If not,
   retake it. The human should only ever see footage you have already checked.
3. **Post.** `review.py add --title "<what it is>" --topic <area> --how "<how to get there and what to judge>" --media <files>`
   - Write `--how` in calm, plain prose: where to look, what changed, what you want judged. Claim only what the footage shows.
   - Several takes go in one `add` (`--media a/clip.mp4 b/clip.mp4`), each path once, each named for what it is
     (rename `clip.mp4` to `bigger-particles.mp4` first). Put your proposal first and comparisons last.
   - Show only what is new. Don't re-post variants the human already decided on.
   - `add` starts the page if it isn't running and pops a desktop notification, so you don't need to alert the human.
4. **Wait.** Run `review.py wait` as a **background** command (`run_in_background`) and end your turn or keep working.
   It exits with the verdict when the human answers. It exits 3 before your prompt cache would go cold: then don't re-arm it.
   The verdict reaches you with the next prompt instead.
5. **Act on the verdict. It is work, not a notification**, even when it arrives as a background-task notice.
   - **REDO**: read the note and any attached images (`list` and `wait` print their paths), rework it, and post the next take
     in the same turn.
   - **OK**: finish it (commit if your project's rules allow), then `review.py handled <id> "committed <hash>"`.
   - Truly need the human's answer before you can go on? `review.py handled <id> "<why>"` and ask them.

The Stop hook keeps this honest. It blocks the end of your turn while a verdict on your item is unread, while a REDO or OK
has neither a newer item from you nor a `handled` mark, or while you have an open item and no `wait` running.

## Other commands

- `review.py list [--open] [--topic <area>]`: what's open, and verdicts with their notes.
- `review.py revoke <id>`: take down your own unanswered item (posted the wrong take). Then post the right one.
- `review.py add --kind bug ...`: file a bug for the human to describe. Fixed one (also by accident)? Close it at once with
  `review.py dismiss <id> "<cause + commit>"`, so nobody is sent after an already-fixed bug.
