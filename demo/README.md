# Demo: Brickfall

A small neon brick breaker with no motion juice yet (no trail, particles, shake or sound). It's the canvas for showing the review loop:
an agent adds game feel one change at a time and posts each change to the inbox, and you answer OK or Redo.

## Run it

```bash
cd demo/brickfall
python -m http.server 8080
```

Open http://localhost:8080/ (mouse or arrow keys), or http://localhost:8080/?auto=1 to let it play itself, which is what
the agent records.

Clips of a web page need Playwright once:

```bash
pip install playwright
playwright install chromium
```

## Try the loop

Copy the game into a folder of its own (so the agent's edits don't land in this repo) and enable the plugin for that
folder only:

```bash
mkdir brickfall-demo && cp demo/brickfall/index.html brickfall-demo/ && cd brickfall-demo && git init
claude plugin marketplace add /path/to/agent-review-inbox --scope local
claude plugin install agent-review-inbox@agent-review-inbox --scope local
```

`--scope local` keeps the hooks out of your other projects. Or skip the install and run `claude --plugin-dir
/path/to/agent-review-inbox` in that folder for a one-off session.

Serve the game in a second terminal (`python -m http.server 8080`), start Claude Code in the folder, and give it one
prompt at a time:

1. "Make the ball leave a short fading trail. The game runs at http://localhost:8080/?auto=1. Record it and post it to
   the review inbox for me."
2. "Bricks should burst into a few particles when they break, and the screen should shake a little. Post both as
   separate takes in one item."
3. "Make the paddle squash and stretch when the ball hits it, and record a clip."

Answer on the inbox page (http://127.0.0.1:7700/). Press **Redo** with a note like "trail is too long, half it" and
watch the agent pick the verdict up and post the next take on its own, then **OK** it.

## Recording the README media

One take of prompt 1 covers everything: post, notification, Redo with a note, retake, OK. Save each capture over the
placeholder of the same name in `assets/`:

| File | What to capture |
|---|---|
| `hero.webp` | The inbox with a few items waiting (a clip, a question, a still), the yellow "agent waiting" glow on each. Under ~10 MB. |
| `step-1-post.png` … `step-4-ok.png` | The loop as four stills: an item arrives, a Redo note, the retake on top with another project's item below, OK. Same size each. |

Also worth having: the Stop hook refusing to let the agent end its turn with an unread verdict.
