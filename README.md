<div align="center">

<h1><img src="assets/logo.svg" alt="" height="26">&nbsp; Agent Review Inbox</h1>

<p><b>A review inbox for agent work that needs human eyes.</b></p>

<p>
  <a href="https://ko-fi.com/trickstack"><img src="https://img.shields.io/badge/Support%20me%20on%20Ko--fi-FF5E5B?logo=ko-fi&logoColor=white" alt="Support me on Ko-fi"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>
  <a href="#install"><img src="https://img.shields.io/badge/Claude%20Code-plugin-D97757" alt="Claude Code plugin"></a>
  <a href="docs/codex-test.md"><img src="https://img.shields.io/badge/Codex-experimental-lightgrey" alt="Codex (experimental)"></a>
</p>

<!-- HERO: the inbox with items from three projects waiting on you. Keep it under ~10 MB so GitHub plays it inline. -->
<img src="assets/hero.webp" alt="Three agents from three projects waiting on you: a web page clip, a question about a slide deck, a game clip" width="820">

</div>

Running a bunch of agent sessions at once? You don't have to keep track of which one did what anymore. Work from all
your agents lands in one inbox: videos of what they built, screenshots, and questions they need you to answer. Hit OK,
Redo with a note, or pick an answer, and it goes straight back to the session that asked, which has to act on it before
it can call the task done.

It's for anything you judge by looking, listening or playing: a UI change, an animation, a particle effect, game feel, a
sound.

A plugin for **Claude Code** (Codex: experimental): one Python file (stdlib only), one HTML page, three hooks and a skill.
The CLI works with any agent that can run a shell command.

## Why

Agents are good at changing code and bad at knowing whether the results are up to your standards. Without a channel for that, you get
"please check the look" buried in a forgotten session, and "needs a human check" lines rotting in notes files. The inbox gives that judgement
one place and closes the loop:

1. **Post.** The agent records a clip or still (`review.py clip`, `shot`, or any media it made), checks the contact sheet
   itself, and posts it with what to look at.
2. **Notify.** A desktop notification pops up; clicking it opens the inbox page.
3. **Verdict.** You watch, then press OK or Redo, with a note and, if you like, a pasted, marked-up screenshot.
4. **Act.** The agent was waiting in the background, cheaply: `wait` gives up before the session's prompt cache goes cold
   (it assumes a one-hour cache; with a shorter one, lower `REVIEW_INBOX_WARM_MIN`), so waiting doesn't cost a full
   context re-read. The verdict arrives with your next prompt instead.
5. **Stay honest.** The Stop hook won't let the agent end its turn while it has an unread verdict, an unanswered Redo or OK,
   or an open item with nothing waiting for the answer.

## What it looks like

<table>
  <tr>
    <td width="50%"><img src="assets/step-1-post.png" alt="The first playable Brickfall, plain grey blocks, posted for review"><br><sub><b>1.</b> An agent posts its work and waits for you.</sub></td>
    <td width="50%"><img src="assets/step-2-redo.png" alt="A note asking for neon and glow typed under the clip, about to press Redo"><br><sub><b>2.</b> You write a note and press Redo.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="assets/step-3-retake.png" alt="The retake: Brickfall with glowing neon bricks"><br><sub><b>3.</b> The retake lands on its own.</sub></td>
    <td width="50%"><img src="assets/step-4-ok.png" alt="The inbox after OK: nothing waiting"><br><sub><b>4.</b> You press OK, and it's done.</sub></td>
  </tr>
</table>

## Install

**Claude Code**

In a session:

```text
/plugin marketplace add trickstack/agent-review-inbox
/plugin install agent-review-inbox@agent-review-inbox
```

From a terminal:

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
plugin hooks until you do). Codex support is experimental: nobody has run it end to end yet. See [docs/codex-test.md](docs/codex-test.md).

**Any other agent**

Point it at `skills/review-inbox/SKILL.md` (paste it into your AGENTS.md or rules file) and have it pass
`--session <any stable id>` so it owns its items. You get the inbox, notifications and `wait`; the turn-end enforcement
needs a hook system that can block the end of a turn.

Needs Python 3.9+ (`winget install Python.Python.3.12`, `brew install python`, `sudo apt install python3`). Without
it the inbox is off, and the SessionStart hook tells the agent so it can offer to install it. Optional capture tools; `review.py doctor` says which are missing and how to install them, and the
SessionStart hook tells the agent too:

| Tool | For | Install |
|---|---|---|
| ffmpeg | screen and window recording, contact sheets | `winget install Gyan.FFmpeg`, `brew install ffmpeg`, `sudo apt install ffmpeg` |
| Playwright | clips, stills and scripted steps on web pages | `pip install playwright && playwright install chromium` |
| pyautogui | scripted steps on desktop apps | `pip install pyautogui` |

macOS needs Screen Recording (and, for desktop steps, Accessibility) permission for your terminal or agent app. On Linux,
screen recording and desktop steps need an X11 session; Wayland isn't supported.

## Use

Nothing to configure. The SessionStart hook tells the agent the inbox exists, and the `review-inbox` skill teaches it the
protocol. The page starts on demand the first time something is posted: http://127.0.0.1:7700/.

Questions land there too. When an agent needs you to decide something, it posts the question with `review.py ask`, optionally with one-click options, instead of asking in a chat you might never look at. The Stop hook catches a turn that ends on a question asked in chat.

The CLI, which you can run yourself too:

| Command | What it does |
|---|---|
| `review.py add --title ... --how ... --media ...` | post an item for review |
| `review.py clip 6 --url <page>` | record a page for 6 s (Playwright), draft `clip.mp4` + contact sheet |
| `review.py clip 6 [--window "Title"]` | record the screen or a window (ffmpeg) |
| `review.py clip --url <page> --do "click text=Save; type hi"` | film an interaction: steps on a page, or `x,y` steps in a `--window` |
| `review.py shot --url ... / --window "Title"` | still of a page, a window or the screen |
| `review.py doctor` | which optional capture tools are missing, and the install commands |
| `review.py wait [<id>]` | block until answered (run in the background) |
| `review.py ask "<question>" [--options A B]` | ask the human; options become one-click answers |
| `review.py list [--open]` | items and verdicts |
| `review.py handled / revoke / dismiss` | close the loop on a verdict, take a wrong take down, close an item nobody needs to answer |
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
| `REVIEW_INBOX_DIR` | `<project>/.review-inbox` | where items live; point several projects at one folder (with one port) to review them all on one page |
| `REVIEW_INBOX_PORT` | `7700` | page port (give each project its own) |
| `REVIEW_INBOX_HOST` | `127.0.0.1` | set `0.0.0.0` to review from your phone over a VPN; there is no auth |
| `REVIEW_INBOX_WARM_MIN` | `50` | idle minutes after which a prompt cache counts as cold |
| `REVIEW_INBOX_CLIP_CMD` | | custom capture command |
| `REVIEW_INBOX_NO_TOAST` | | no desktop notifications |
| `REVIEW_INBOX_NO_SERVER` | | `add` doesn't start the page server (run `review.py serve` yourself) |
| `REVIEW_INBOX_SESSION` | | the session id for agents without `CLAUDE_CODE_SESSION_ID` (same as `--session`) |
| `REVIEW_INBOX_BY` | | default `--by` label on posts |
| `REVIEW_INBOX_BEAT_S` | `5` | how often `wait` checks for an answer |
| `REVIEW_INBOX_AVF_INPUT` | `1:none` | macOS: the avfoundation device to record |

Notifications: Windows toast (click opens the page), macOS Notification Center, Linux `notify-send`, otherwise none.

## Demo

[`demo/`](demo/) is a small neon browser brick breaker with no motion juice yet, for trying the loop: ask the agent to add a ball trail,
particles or screen shake, and review each change.

## Test

```bash
python tests/selfcheck.py
```

## Credits

Built with Claude (Anthropic's Claude Opus 5.5 via Claude Code).

## License

[MIT](LICENSE)

Using it? Questions, ideas and setups go in [Discussions](https://github.com/trickstack/agent-review-inbox/discussions); something broken goes in [Issues](https://github.com/trickstack/agent-review-inbox/issues).

If it saved you some time, you can [buy me a coffee](https://ko-fi.com/trickstack).
