# The developer page

A page for working on Desk Talk: run the tests, watch pictures turn into object states, see how the Pi
and every part of the app are doing, look inside the database, and see what's stored. It only answers
requests from this computer.

It's a small Vite + React + TypeScript app. The app page itself (`web_app/`) stays plain JavaScript.

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

`npm run dev` needs the server running too. It reads the two ports from `config/settings.yaml` (and
`config/settings.local.yaml` if you have one): `web.port` for the server and `dev.vite_port` for itself.
Open it at `localhost`, not `127.0.0.1`: the server only lets `http://localhost:<dev.vite_port>` in.

## How it's laid out

- `src/api.ts`: every call to the server, one function per route, and the shape of each reply. A call
  that fails throws `ApiError` (`status` 0 when the server didn't answer).
- `src/usePoll.ts`: asks the server again every few seconds and keeps the last good answer.
- `src/App.tsx`: the tab bar. It loads `/api/dev/settings` once and gives it to every tab. The page keeps
  no numbers of its own: they all come from `config/settings.yaml` through that reply.
- `src/tabs/`: one file per tab (Checks, Live, System, Database, Storage).
- `src/ui.tsx`, `src/format.ts`, `src/style.css`: pieces the tabs share (tables, labels, sizes and times
  in words, the look).
