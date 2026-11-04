# Checking that everything works

One place to see that Desk Talk is healthy, and how to check each item from the meeting of 2026-10-24.

Desk Talk runs on the Pi, in Docker. The developer page below is for working on it, so it runs on a computer with `server.py`
(it is switched off on the Pi). To check the Pi itself, go to [The Pi](#the-pi) at the end.

## The quick way: the developer page

1. **On your Mac**, build the page once (and again after `git pull` changes `dev_ui/`):

   ```bash
   cd dev_ui && npm install && npm run build && cd ..
   ```

2. **On your Mac**, start Desk Talk. It needs YOLO, which `uv sync --all-packages` brings in. For a camera, put `camera:` then `source: webcam` in `config/settings.local.yaml` (or `phone`):

   ```bash
   uv run python server.py
   ```

3. Open http://localhost:5000/dev/ in a browser on the Mac (it only answers this computer).

| Tab | What to look at | Good looks like |
|---|---|---|
| **Checks** | Press **Run everything**. It runs, one after the other: the fast tests, lint (ruff), types (pyright), dead code (vulture) and the page's own tests. | All five chips say *passed*. A failed test is listed at the top with its message and a **Run again** button. Any single folder, file or test has its own **Run** button. |
| **Live** | Put the bottle on the desk, wait a few seconds, then take it away. | A `bottle` card turns PRESENT, the changes list shows `appeared` then `removed`, and the smoothing chart shows the confidence above the 0.5 line, the vote filling up and the wait before each change. A "flickering" badge means an object is coming and going too often. |
| **System** | The Pi's group, then the rest. | Every row green. Red or amber rows name the problem: the Pi too hot or slowed down, little disk left, slow detection, dropped events, a background job that keeps failing, the AI not ready. |
| **Database** | Click `events`, then the ready-made queries. | The bottle's `appeared` / `removed` rows are there, with the times you put it down and took it away. The page can't change the database. |
| **Storage** | What the browser and the data folder keep. | The app page's chat history, to-do list and habit counters, and today's event log in the data folder. |
| **Demo** | Pick a pretend warning (cup near the desk edge, drink next to the laptop, time for a drink, camera covered, a sip, keys gone missing), set the seconds, press **Start**. Open the app page next to it. | The countdown runs down on the page, then the warning shows up in the app's chat and as a Mac pop-up, exactly like a real one. **Cancel** stops one before it goes off. |

The developer page changes nothing in the app, with two exceptions: starting and stopping test runs (and
deleting a localStorage key after you say yes), and the **Demo** tab, which fires pretend warnings on
purpose. Those go out through the same parts as real ones, so they land in the chat, the Mac pop-ups and the
event log (each alert marked `"demo": true` there), and a pretend sip counts in today's water. The list is in
`dev.scenarios` in `config/settings.yaml`.

## Each meeting item

| # | Item | Status | How to check it yourself |
|---|---|---|---|
| 1 | Run the whole system and test it with the bottle on the desk | Done on the code before the cleanup. The Pi now runs it all by itself; a bottle on the real desk is the "A cup on the desk" step in [PI_CHECKS.md](PI_CHECKS.md), not tried yet | The Pi steps below, then the **Live** and **Database** tabs (on a computer). |
| 2 | A simple test that observations turn into states and events | Done: `tests/integration/test_app_observation_to_events.py` | `uv run pytest tests/integration/test_app_observation_to_events.py -v`, or find it in the **Checks** tab and press its **Run**. |
| 3 | Tidy the worktrees, merge the branches, fix conflicts, test on main | Done: PRs #5 to #8 merged, only the worktrees still in use are left | `git worktree list`, `gh pr list --state all`, then **Run everything** on main. |
| 4 | A developer page with the tests, observability, the database and local storage; the app page's stack | Done: this page. The app page's stack is in [the plan](plans/2026-10-24-developer-ui-plan.md#answer-to-7-the-stack-of-the-current-app-page) | The table above. |
| 5 | List and sort the FastAPI routes | Done: [API.md](API.md) | `tests/characterization/test_route_coverage.py` fails if a route is missing from the cases; the page's routes are in API.md too. |
| 6 | What the Pi sends, how much, and a plan for pictures | Settled by a bigger change: the pictures now go from the camera container to the app on the same Pi, not over Wi-Fi. [PI_TRANSFER.md](PI_TRANSFER.md) keeps the sizes and the history | Read its status note. |

## The same checks from the terminal

**On your Mac**, from the project folder:

```bash
uv run pytest -m "not slow and not docker and not e2e" --cov
```

```bash
uv run ruff check . && uv run pyright && uv run vulture
```

```bash
npm --prefix dev_ui test
```

```bash
uv run pytest -m e2e
```

The last one drives the app page and the developer page in a real browser (`uv run playwright install chromium` once). `uv run pytest -m slow` runs the real YOLO model, when you change detection.

`DESKTALK_PI=pi uv run pytest -m pi -v -rs` runs against the real Pi over SSH, and [PI_CHECKS.md](PI_CHECKS.md) lists what only a person can do there: a cup on the desk, a power cut, the Wi-Fi off, listening to the speaker, closing the laptop, a real phone.

## The Pi

Everything runs on the Pi, in three containers. See [raspberry_pi/README.md](../raspberry_pi/README.md) for the first install, which is one command.

**On the Pi**, to get the newest code and rebuild all three:

```bash
bash ~/desk-talk/raspberry_pi/update.sh
```

```bash
cd ~/desk-talk/raspberry_pi && docker compose ps
```

`docker compose ps` should say `healthy` for all three after a minute or so. Then open `http://desktalk.local:5000` on a phone or
computer on the same Wi-Fi: the live picture should move, and `curl http://desktalk.local:5000/api/health` should show the detector `ready`
under `pi`. The developer page is off on the Pi, so `/api/health` and `docker compose logs` take its place there.

**On your Mac**, to run the checks against the real Pi (needs a key login to `ssh pi`; every test skips without `DESKTALK_PI`):

```bash
DESKTALK_PI=pi uv run pytest -m pi -v -rs
```

A skip isn't a pass: it means a person has to do that part ([PI_CHECKS.md](PI_CHECKS.md)).
