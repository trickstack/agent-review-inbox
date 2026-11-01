# agent-review-inbox

**A review inbox for agent work that needs a human's eyes.** Your coding agent finishes something you can only judge by
looking, listening or playing (a UI change, an animation, a particle effect, game feel, a sound), records it, and posts it
to one page. You answer **OK** or **Redo** with a note. The verdict wakes the agent, and a hook makes sure it acts on it.

A plugin for **Claude Code** and **Codex**: one Python file (stdlib only), one HTML page, three hooks and a skill.
The CLI works with any agent that can run a shell command.

<!-- GIF: the whole loop in ~15 s: agent posts, notification pops, Redo with a note, agent posts the retake -->
![The loop](docs/media/loop.gif)

## Why

Agents are good at changing code and bad at knowing whether the result *looks right*. Without a channel for that, you get
"how does it look?" buried in chat, "needs a human check" lines rotting in notes files, and several parallel sessions each
waiting on you in a different window. The inbox gives that judgement one place and closes the loop:

1. **Post.** The agent records a clip or still (`review.py clip`, `shot`, or any media it made), checks the contact sheet
   itself, and posts it with what to look at.
2. **Notify.** A desktop notification pops up; clicking it opens the inbox page.
3. **Verdict.** You watch, then press OK or Redo, with a note and, if you like, a pasted, marked-up screenshot.
4. **Act.** The agent was waiting in the background, cheaply: `wait` gives up before the session's prompt cache goes cold,
   so it never costs a full context re-read. The verdict arrives with your next prompt instead.
5. **Stay honest.** The Stop hook won't let the agent end its turn while it has an unread verdict, an unanswered Redo or OK,
   or an open item with nothing waiting for the answer.

<!-- screenshot: inbox page with an "agent waiting" card glowing -->
![The inbox page](docs/media/inbox.png)

## Install

**Claude Code**

```bash
claude plugin marketplace add trickstack/agent-review-inbox
claude plugin install agent-review-inbox@agent-review-inbox
```

Or try it for one session without installing: `claude --plugin-dir /path/to/agent-review-inbox`.

**Codex**

```bash
codex plugin marketplace add trickstack/agent-review-inbox
```

Then install it from `/plugins` in `codex`, start a new session, and trust its three hooks in `/hooks` (Codex skips
plugin hooks until you do). Codex support is new; see [docs/codex-test.md](docs/codex-test.md).

**Any other agent**

Point it at `skills/review-inbox/SKILL.md` (paste it into your AGENTS.md or rules file) and have it pass
`--session <any stable id>` so it owns its items. You get the inbox, notifications and `wait`; the turn-end enforcement
needs a hook system that can block the end of a turn.

Needs Python 3.9+. Optional: `ffmpeg` (screen recording, contact sheets) and Playwright (`pip install playwright &&
playwright install chromium`) for clips and stills of web pages.

## Use

Nothing to configure. The SessionStart hook tells the agent the inbox exists, and the `review-inbox` skill teaches it the
protocol. The page starts on demand the first time something is posted: http://127.0.0.1:7700/.

The CLI, which you can run yourself too:

| Command | What it does |
|---|---|
| `review.py add --title ... --how ... --media ...` | post an item (`--kind bug` for a bug to describe) |
| `review.py clip 6 --url <page>` | record a page for 6 s (Playwright), draft `clip.mp4` + contact sheet |
| `review.py clip 6 [--window "Title"]` | record the screen or a window (ffmpeg) |
| `review.py shot --url ...` | still of a page |
| `review.py wait [<id>]` | block until answered (run in the background) |
| `review.py list [--open]` | items and verdicts |
| `review.py handled / revoke / dismiss` | close the loop on a verdict, take a wrong take down, close a fixed bug |
| `review.py serve` | the page |

Items live in `<project>/.review-inbox/<id>/` (one folder each, so parallel sessions never write the same file). The
folder ignores itself in git.

### Your own recorder

Games and native apps usually have a better capture path than a screen grab (an engine's own viewport recorder, a
screen recorder's replay buffer). Point `clip` at it:

```bash
export REVIEW_INBOX_CLIP_CMD='my-recorder --last {seconds} --out "{out}"'
```

### Settings

| Variable | Default | |
|---|---|---|
| `REVIEW_INBOX_DIR` | `<project>/.review-inbox` | where items live |
| `REVIEW_INBOX_PORT` | `7700` | page port (give each project its own) |
| `REVIEW_INBOX_HOST` | `127.0.0.1` | set `0.0.0.0` to review from your phone over a VPN; there is no auth |
| `REVIEW_INBOX_WARM_MIN` | `50` | idle minutes after which a prompt cache counts as cold |
| `REVIEW_INBOX_CLIP_CMD` | | custom capture command |
| `REVIEW_INBOX_NO_TOAST` | | no desktop notifications |

Notifications: Windows toast (click opens the page), macOS Notification Center, Linux `notify-send`, otherwise none.

## Demo

[`demo/`](demo/) is a deliberately plain browser brick breaker for trying the loop: ask the agent to add a ball trail,
particles or screen shake, and review each change.

## Test

```bash
python tests/selfcheck.py
```

## Credits

Built with Claude (Anthropic's Claude Opus 5.5 via Claude Code).

## License

[MIT](LICENSE)
