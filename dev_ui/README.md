# The developer page

A page for working on Desk Talk: run the tests, watch pictures turn into object states, see how the Pi
and every part of the app are doing, look inside the database, and see what's stored. It only answers
requests from this computer.

It's a small Vite + React + TypeScript app. The app page itself (`web_app/`) stays plain JavaScript.
You need Node 22 (it comes with npm).

## The five tabs

- **Checks**: **Run everything** runs each step in `dev.checks` (the fast tests, the lint, the type check)
  one after the other. Below it, every test as folder → file → test, with a search box and a **Run** button
  on each. While a run goes you see each test pass or fail as it finishes, the printout, and **Stop**.
  Afterwards the failed tests come first, with their messages, and **Run again** runs just those.
- **Live**: a card per object with its state (present, absent, unknown) and for how long, the changes
  newest first (the newest `dev.sql_max_rows` of them, with a "flickering" badge on an object that keeps
  flipping), and a chart of the smoothing
  for one object over its last `object_states.trace_pictures` pictures: the box's confidence against the
  on and off lines, the vote, the state, and the wait before it changes.
- **System**: how the Pi is doing (connected, pictures a second, last picture, its detector, temperature,
  slowed down, disk, clock) and every part on this computer (detection, the event stream, background jobs,
  timed jobs, the event log, the AI), from `/api/health` and `/api/log/status`. Numbers turn amber or red
  at the limits in `dev.health`.
- **Database**: the databases and their tables with row counts (counted on open, every
  `dev.table_counts_every_sec` and on **Count again**). Click a table to see its newest rows and
  its `CREATE` line, or type a query (Ctrl/Cmd+Enter runs it) or pick a ready-made one. Only queries that
  read can run; the database is opened read-only.
- **Storage**: what the app page keeps in this browser (localStorage), what each key is for, and a
  **Delete** button that asks first; the files in the data folder, with a look at the start of each (read
  again every `dev.table_counts_every_sec` and on **Read the folder again**); and
  the Pi's stored photos and free space.

## Use it

On your Mac, once (and again after you change anything in `dev_ui/`):

```sh
cd dev_ui
npm install
npm run build        # puts the page in dev_ui/dist/
```

Then start Desk Talk as usual (`uv run python server.py`) and open http://localhost:5000/dev/.

## Work on it

```sh
cd dev_ui
npm run dev          # serves the page at http://localhost:5173/dev/ and passes /api to the server
npm test             # the page's tests (Vitest), no server needed
npm run build        # checks the types, then builds dist/
```

Run `npm test` and `npm run build` before you push: GitHub runs both (with `npm ci`) on every push, in the
`page` job of `.github/workflows/tests.yml`. Versions are pinned exactly in `package.json`, and
`package-lock.json` is checked in.

`npm run dev` needs the server running too. It reads the two ports from `config/settings.yaml` (and
`config/settings.local.yaml` if you have one): `web.port` for the server and `dev.vite_port` for itself.
Open it at `localhost`, not `127.0.0.1`: the server only lets `http://localhost:<dev.vite_port>` in.
The Storage tab only sees the app page's localStorage at http://localhost:5000/dev/, because a browser
keeps one localStorage per address.

The server's `/api/dev/...` routes are listed in [docs/API.md](../docs/API.md#developer-page-this-computer-only-15).

## How it's laid out

- `src/api.ts`: every call to the server, one function per route, and the shape of each reply. A call
  that fails throws `ApiError` (`status` 0 when the server didn't answer).
- `src/usePoll.ts`: asks the server again every few seconds and keeps the last good answer.
- `src/App.tsx`: the tab bar. It loads `/api/dev/settings` once and gives it to every tab. The page keeps
  no numbers of its own: they all come from `config/settings.yaml` through that reply.
- `src/tabs/`: one file per tab (Checks, Live, System, Database, Storage).
- The sums behind the tabs: `src/testTree.ts` (Checks), `src/timeline.ts` and
  `src/SmoothingChart.tsx` (Live), `src/health.ts` (System), `src/queries.ts` (Database's ready-made
  queries), `src/browserStorage.ts` (Storage).
- `serverPorts.ts`: reads `web.port` and `dev.vite_port` for `vite.config.ts`.
- Tests sit next to the file they test (`*.test.ts`, `*.test.tsx`) and fake the server's replies, so they
  never need the server.
- `src/ui.tsx`, `src/format.ts`, `src/style.css`: pieces the tabs share (tables, labels, sizes and times
  in words, the look).
