# Checking that everything works

One place to see that Desk Talk is healthy, and how to check each item from the meeting of 2026-10-24.

## The quick way: the developer page

1. **On your Mac**, build the page once (and again after `git pull` changes `dev_ui/`):

   ```bash
   cd dev_ui && npm install && npm run build && cd ..
   ```

2. **On your Mac**, start Desk Talk:

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

## Each meeting item

| # | Item | Status | How to check it yourself |
|---|---|---|---|
| 1 | Run the whole system and test it with the bottle on the desk | Done on the code before the cleanup; run again after updating the Pi | The Pi steps below, then the **Live** and **Database** tabs. |
| 2 | A simple test that observations turn into states and events | Done: `tests/integration/test_app_observation_to_events.py` | `uv run pytest tests/integration/test_app_observation_to_events.py -v`, or find it in the **Checks** tab and press its **Run**. |
| 3 | Tidy the worktrees, merge the branches, fix conflicts, test on main | Done: PRs #5 to #8 merged, only the worktrees still in use are left | `git worktree list`, `gh pr list --state all`, then **Run everything** on main. |
| 4 | A developer page with the tests, observability, the database and local storage; the app page's stack | Done: this page. The app page's stack is in [the plan](plans/2026-10-24-developer-ui-plan.md#answer-to-7-the-stack-of-the-current-app-page) | The table above. |
| 5 | List and sort the FastAPI routes | Done: [API.md](API.md) | `tests/characterization/test_route_coverage.py` fails if a route is missing from the cases; the page's routes are in API.md too. |
| 6 | What the Pi sends, how much, and a plan for pictures | Done as a plan: [PI_TRANSFER.md](PI_TRANSFER.md) | Read its open questions; nothing in the code changed yet. |

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

## The Pi

The camera program and the detector need the newest code too. See [raspberry_pi/README.md](../raspberry_pi/README.md) for the first setup.

**On the Pi:**

```bash
cd ~/desk-talk && git pull
```

```bash
cd ~/desk-talk/raspberry_pi/detector_service && docker compose config >/dev/null && docker compose up -d --build && docker compose ps
```

`docker compose ps` should say `healthy` after a minute or so.

```bash
sudo systemctl restart desk-talk-camera
```

(or start it by hand with `python3 -m raspberry_pi.camera --server http://<your Mac's address>:5000 --key <the pairing key>`).

Then, **on your Mac**, the **System** tab's Pi group should fill in, and the **Live** tab should show what's on the desk.
