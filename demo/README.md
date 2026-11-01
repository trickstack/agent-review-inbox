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
3. "Colour the brick rows warm to cool from the top and post a still."

Answer on the inbox page (http://127.0.0.1:7700/). Press **Redo** with a note like "trail is too long, half it" and
watch the agent pick the verdict up and post the next take on its own, then **OK** it.

## Recording the README media

One take of prompt 1 covers everything: post, notification, Redo with a note, retake, OK. Save each capture over the
placeholder of the same name in `assets/`:

| File | What to capture |
|---|---|
| `hero.gif` | The whole loop in ~15 s: agent posts, notification, Redo with a note, retake arrives, OK. Under ~10 MB. |
| `toast.png` | The desktop notification as the agent posts (crop to the notification). |
| `inbox.png` | The inbox page with the item, ideally with the yellow "agent waiting" glow. |
| `redo.gif` | Typing a Redo note (maybe pasting a marked-up screenshot) and pressing Redo. |
| `retake.png` | The agent's terminal picking up the verdict, or the new card arriving on the page. |
| `ok.png` | The retake's card after pressing OK. |

Also worth having: the Stop hook refusing to let the agent end its turn with an unread verdict.
