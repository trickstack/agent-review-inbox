# Demo: Brickfall

A deliberately plain brick breaker (flat grey boxes, no effects, no sound). It's the canvas for showing the review loop:
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

Start Claude Code in the repo root with the plugin loaded:

```bash
claude --plugin-dir .
```

Then give it prompts like these, one at a time:

1. "In demo/brickfall, make the ball leave a short fading trail. Record it with the review inbox and post it for me."
2. "Bricks should burst into a few particles when they break, and the screen should shake a little. Post both as separate takes in one item."
3. "Colour the brick rows warm to cool from the top and post a still."

Answer on the inbox page (http://127.0.0.1:7700/). Try a **Redo** with a note like "trail is too long, half it" and
watch the agent pick the verdict up and post the next take on its own.

## Recording the README media

Shots worth capturing for the README:

- The desktop notification popping up as the agent posts.
- The inbox card with the yellow "agent waiting" glow while `wait` runs in the background.
- Typing a Redo note, pasting a marked-up screenshot, pressing Redo.
- The agent's terminal picking up the verdict and posting the retake.
- The Stop hook refusing to let the agent end its turn with an unread verdict.
