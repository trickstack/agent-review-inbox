# Codex test run

The Codex side is built from OpenAI's plugin and hooks docs but hasn't been run in Codex yet. This is a ~20 minute
checklist for someone with Codex access. Note what happens at each step, especially anything that differs from
"Expected".

You need: Codex CLI (logged in), Python 3.9+, git. Optional: `pip install playwright && playwright install chromium`
so the agent can record clips of the demo game.

## 1. Install

```bash
git clone <repo url> agent-review-inbox
codex plugin marketplace add ./agent-review-inbox
```

Start `codex`, open `/plugins`, install **agent-review-inbox**, then quit and start a new session. Open `/hooks` and
trust the plugin's three hooks (SessionStart, UserPromptSubmit, Stop). Codex skips plugin hooks until you do.

- Expected: the plugin is listed and installs. If `marketplace add` or the install fails, copy the exact error.

## 2. Does the agent know about the inbox?

In a fresh session started in the `agent-review-inbox` folder, ask: *"Is there a review inbox in this project? What
command would you use to post to it?"*

- Expected: it mentions the inbox and quotes a command ending in `review.py --session <id>`. That text comes from the
  SessionStart hook. If it has no idea, the hook didn't run (check `/hooks`).

## 3. Post something

In one terminal: `cd demo/brickfall && python -m http.server 8080`. Then ask Codex: *"In demo/brickfall, make the ball
leave a short fading trail. Record it and post it to the review inbox for me."*

- Expected: a desktop notification, and a card on http://127.0.0.1:7700/.
- Expected: Codex then runs `review.py ... wait` (in the background if Codex can; otherwise it may just block waiting,
  which is fine). Write down which.
- If it ends its turn without posting or waiting, the Stop hook should have pushed it back once to arm `wait`. Did it?

## 4. Redo

On the page, type a note like "trail is too long, half it" and press **Redo**.

- Expected: Codex picks the verdict up (from the running `wait`, or with your next message), reworks it and posts a new
  card on its own.

## 5. The Stop hook

Post a Redo again, then send Codex an unrelated message ("what's 2+2?").

- Expected: it answers, but can't finish the turn: the Stop hook continues it with the verdict until it posts a new take
  or runs `review.py handled <id> "<why>"`.

## Report back

Codex version (`codex --version`), OS, and for each step: worked / didn't / something else, plus any error text and a
screenshot or two. That's enough to fix whatever differs.
