# Cloud Server Design

## Why

Desk Talk's camera sees a person's habits but keeps that history at home. The cloud server collects non-sensitive events from each person's Pi and uses an LLM to write them a weekly report that helps them reach their goals.

The project will be open source. Each person runs the "cloud" server (website, API and database) on their own computer, in Docker, with their own API keys. Hosting on Vercel and Railway comes later, so nothing here should make that hard.

## What a person does

1. Starts the server with `docker compose up`, after filling in `.env`.
2. Opens the website and logs in with Google.
3. Clicks "Add a Pi" and gets a one-time code and a setup command to run.
4. Runs the command. The Pi is linked to their account, with its own token.
5. Reads a new report on the website each week, and can see each Pi's state and events.

**Until the Pi refactor is finished** (`docs/plans/2026-10-30-pi-standalone-refactor-plan.md`), `desktalk` runs on the computer, so the sender runs there too and the setup command only links it. The refactor's `install.sh` then does the full setup. The sender moves to the Pi with the `app` container, with no rewrite.

## Goals

- Log in with Google, and link a Pi with a one-time code.
- `POST /v1/events`, `GET /v1/devices/{id}/state` and `GET /v1/devices/{id}/events?since=xxx`, authenticated with a token in a request header.
- Tokens saved in Postgres.
- A weekly job that writes each Pi's report with an LLM, shown on the website.
- A simple admin login with username and password.
- Everything in Docker, started with one command.
- Chat AI keys stay on this server. The Pi can use them through this server, or not at all.

## Non-goals

- Deploying to Vercel or Railway, or HTTPS. Plain HTTP on the home network for now.
- Email or phone notifications. Website only.
- Payments, teams, sharing, or more than one report format.
- Fixing the Pi's clock when it has no internet (noted in the refactor plan).

## What must never happen

- A camera picture reaches this server. This holds for events and for chat AI requests.
- To-do text is sent to this server as events, or is stored or logged here. The one place it passes through is a chat request (below), by the user's choice.
- The Pi or the desktop app slows down, stops or loses a feature because this server is off, slow or never set up.
- A raw token, API key or Google secret appears in the database, the logs or the git repo.
- A report states something the data doesn't support. The tested prompt's rules stay.

## What gets sent

The user's rule: events and state only, no pictures. Goals yes, to-do text no. Steady changes only, no per-picture detections.

Only these event types are sent. Anything else stays home:

| Type | Fields sent | Note |
|---|---|---|
| `object_state` | `ts`, `id`, change, object, prev_state, new_state, device_id, conf_avg, dwell_sec | Leave out `bbox` (my call; the report doesn't use it) |
| `habit` | `ts`, `id`, habit, action, numbers from `extra`, and `how` and `healthy` | Never `say`: `todos.py` puts the to-do's text in it. `how` only from a fixed list of words, so the report can tell a logged sip from a camera guess, or a real break from the camera being off (my call) |
| `nudge` | `ts`, `id`, habit, rule, agent, evidence | Never `text`. For `habit: tasks`, leave out `evidence` too: it quotes the to-do ("starting with "english essay"") |
| `rule_outcome` | `ts`, `id`, rule, habit, result, after_sec | |
| `alert` | `ts`, `id`, kind, agent, scenario, feature | Leave out `text` (my call: it's the character's wording) |
| `camera` (`CameraNotice`) | `ts`, `id` and its fixed fields | The report uses it to judge whether data is reliable |
| `goals` (new) | `ts`, `id`, wake, bed, the goal numbers in the report's Goals section | Sent when linked, when they change, and daily |
| `daily_totals` (new) | `ts`, day, desk minutes, hours at desk, water ml and sips, breaks, longest sit, phone-free minutes, phone-on-desk minutes, sunlight minutes | State, from the same day files `build_weekly_input.py` reads. Sent when the numbers change, at most every `cloud.totals_every_min`, and the newest values for a day replace the older ones (my call: the tested prompt depends on these, and they can't be rebuilt reliably from events) |

`detection`, `agent_joined`, `agent_left` and `compaction` are not sent. From `extra`, only number values go, for example water in ml, plus the two fields named above. The food `label` stays home. The filter is a function in `shared/desktalk_shared/cloud_api.py`, tested by feeding it every event type with a to-do and a fake picture field in it. The server checks the same rules again and drops any field it doesn't know, so an old or wrong sender can't store more.

Events from the Demo tab don't get sent. `DemoFirer` doesn't mark all of its events today (`_sip` and `_camera_covered` set no `demo` mark), so that gets fixed as part of this work.

## How it's built

```
Pi or computer (desktalk)                     The server (cloud/compose.yaml)
  EventLog ──► CloudUploader ── token ──────► api      FastAPI: /v1/* and /api/*
               (PeriodicWorker)                 │
  OpenAICompatibleChatModel ─ token (optional)─►│
                                                ▼
  Browser ───► web  (React, nginx) ──/api────► postgres ◄── reports (weekly job)
```

**Folders**
- `cloud/server/`: the Python package `desktalk_cloud`, a member of the uv workspace. Never imports `desktalk/`.
- `cloud/web/`: React, TypeScript and Vite, the same tools and versions as `dev_ui/`. It builds to static files served by nginx, which passes `/api`, `/auth`, and `/v1` to `api`. Being on one address keeps login cookies simple, and Vercel rewrites can do the same job later.
- `cloud/compose.yaml`, with four containers: `postgres`, `api`, `web` and `reports`.
- `cloud/settings.yaml` for numbers, `cloud/prompts.yaml` for the report's prompt and wording, `cloud/.env.example` for secrets.
- `shared/desktalk_shared/cloud_api.py`: what's sent and the filter. Standard library only, because this runs on the Pi too.

**Same rules as the rest of the repo:** interfaces first with contract suites, a `Clock`, config not code, no network in fast tests, plain words. A new `cloud/server/.../ports.py` holds `Store`, `ReportWriter` (LLM) and `GoogleLogin`, each with a real version and a fake.

**Database tests:** the fast tests use an in-memory `Store`. The same contract suite runs against real Postgres under the `docker` marker. Migrations use Alembic.

## Database

- `users`: id, google_sub (unique), email, name, created_at.
- `admins`: username, password_hash.
- `devices`: id (a UUID), user_id, name, time_zone, created_at, last_seen_at, caught_up_at, removed_at.
- `daily_totals`: device_id, day, payload (JSONB), updated_at. Unique on (device_id, day).
- `device_tokens`: id, device_id, token_hash, created_at, last_used_at, revoked_at.
- `pairing_codes`: code_hash, user_id, expires_at, used_at.
- `events`: device_id, event_id, ts, type, payload (JSONB), received_at. Unique on (device_id, event_id), so a repeat upload changes nothing. Indexed on (device_id, ts).
- `device_state`: device_id, label, state, since, updated_at. One row per object, updated on upload; an older event never overwrites a newer one.
- `reports`: id, device_id, week_start, status (pending, done, no_data, failed), markdown, model, error, created_at. Unique on (device_id, week_start).

**Saving tokens (my call):** you asked for tokens saved in the database. It stores a hash, like a password, so a leaked database can't be used to upload. The Pi holds the real token, in a file in its `data` folder.

## Linking a Pi and its token

1. The website calls `POST /api/devices/code` with the browser's time zone, which becomes the Pi's (my call: a person's browser is more likely to be right than a Pi's setting). The server makes a one-time code that lasts `pairing.code_valid_min` (10), and shows the setup command with this server's address (`server.public_url`).
2. The Pi calls `POST /v1/devices/claim` with the code and a name. The reply has the `device_id` and the token, once only.
3. Each request after that carries `Authorization: Bearer <token>`.
4. The website can rename a Pi or remove it. Removing it revokes the token at once and keeps its history, and the Pi's sender stops trying until it's linked again. "Delete with its data" is a separate button.
5. A kept Pi's page has "Link again", which makes a code tied to that same device. So the history carries on after a removal, a reinstall, or the move from the computer to the Pi in the refactor. A new "Add a Pi" code always makes a new device (my call).

A code works once. A wrong code gets the same reply as an expired one, and tries are limited per address.

## API

Pi routes use the token. Website routes use a login cookie. Each person only reaches their own Pis, and anything else gets 404.

| Route | Who | What |
|---|---|---|
| `POST /v1/devices/claim` | One-time code | Links a Pi (above) |
| `POST /v1/events` | Token | Up to `ingest.max_batch` (500) events, with `caught_up: true` on the batch that empties the sender's backlog. Replies with how many were accepted, how many were repeats, and which were refused and why. One bad event never fails the batch, so the Pi can't get stuck on it. Size limit `ingest.max_body_kb` |
| `GET /v1/devices/{id}/state` | Token or cookie | Goals, last seen, last camera notice, and each object's current state |
| `GET /v1/devices/{id}/events?since=xxx` | Token or cookie | Events after `since` (Unix seconds, like `Event.ts`), oldest first, up to `limit`. Also returns `next_since` and `next_after_id`, passed back as `after_id` so events with the same time aren't skipped |
| `POST /v1/chat/completions` | Token | Text-only chat through this server's API keys. Refuses any image content. The text, which can include to-dos and what the user typed, passes through to the AI company unchanged and is never stored or logged here, including in error messages (the user's choice, so characters keep track of to-dos). Replies 503 when no key is set, and is limited per Pi. It accepts what `OpenAICompatibleChatModel` sends, and forwards to one provider set by `chat.url` and `chat.model`, ignoring the model name the app asks for. This work only builds the server's route: the app starts using it in Task 7 of the refactor plan (my call) |
| `/auth/google/login`, `/auth/google/callback`, `/auth/logout` | Anyone | Google login, and a signed cookie |
| `/api/me`, `/api/devices...`, `/api/reports...` | Cookie | The website's data. `DELETE /api/me` removes the person and all their data |
| `/auth/admin/login`, `/api/admin/...` | Admin cookie | Users, Pis, last seen, failed reports, and re-running one report |

**Admin (my call):** `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env` make the admin account on first start. The password is stored hashed. With neither set, there is no admin login. Admins see no events or report text, only whether things are working.

## The sender in `desktalk`

`CloudUploader`, a `PeriodicWorker` in `desktalk/core/` behind a new `CloudLink` interface, built in `bootstrap.py`. Linking saves the server's address and the token, so there's nothing to set in `config/settings.yaml`. Until it's linked, the job does nothing and calls nothing.

- Each round, it reads new events from the `EventStore` from a saved place (`KeyValueStore`), filters them, and sends a batch. The saved place starts at the moment of linking, so a month of old history isn't uploaded by surprise (my call). "Link again" keeps the place.
- **The event log is the waiting list (my call):** there's no second copy. With no Wi-Fi, the place stops moving and catches up later. Anything older than `event_log.keep_days` is gone, and that's accepted.
- Failures wait longer each time (`desktalk_shared.backoff`). A failure is only logged. A 401 stops sending and shows "not linked any more".
- It never slows down the event stream, because it's a job of its own.
- `scripts/link_cloud.py --server <url> --code <code>` does the linking for now, and `install.sh` does it after the refactor.

## The weekly report

- **Starting point:** `data/weekly_report_test/prompt.md` and `build_weekly_input.py` (git-ignored now). The prompt moves to `cloud/prompts.yaml`, and the data builder moves to `desktalk_cloud`, reading from Postgres. To-dos come out of both.
- **When:** the `reports` container checks every few minutes. A report is due for each Pi at `reports.day` and `reports.hour` (Sunday 20:00) in that Pi's time zone, for the 7 days just finished. This runs on people's own computers, which may be asleep at that time, so a missed week gets written the next time it runs (my call). The unique row on (device_id, week_start) means a week can't be written twice.
- **Late data (my call):** a report waits until `caught_up_at` is after the end of the week, or until `reports.wait_for_pi_days` (2) has passed, whichever comes first. This stops a Pi that hasn't caught up from reading as "the app was off". If the wait runs out, the report's data notes say when this Pi was last heard from, so the report doesn't count those days as the app being off. Events that arrive after that don't rewrite it, but the admin page can re-run it.
- **No data that week:** saved as `no_data` once the same wait is over, with no LLM call and no cost.
- **Which LLM:** `reports.provider` and `reports.model`. `anthropic` (default, `claude-sonnet-5-5`, which did best in the test on one week of thin data) or `openai_compatible` with a URL, for Gemini, OpenAI, Groq or Ollama. The key comes from `.env`. With no key, the report is marked `failed` with a plain message and nothing else breaks.
- **Failures:** tried again `reports.max_tries` (3) times with growing waits, then `failed`. The admin page can re-run it.
- **Nudge outcomes:** use `rule_outcome` when there is one, otherwise the "next sign" estimate from `build_weekly_input.py`. The other chat found that outcomes are only recorded for newer rules.

## The website

Pages: login, Pis (list, add, rename, remove), one Pi (state, recent events), reports (a list, and one report as markdown, with raw HTML switched off), and admin. Plain and simple. A new report shows as a "new" mark on the website, the only notification.

**Google login on your own computer:** each person makes their own Google client ID and secret, and the README walks through it. Google only allows `localhost` or a real domain as the address, so people log in on the computer running the server. The Pi still uses that computer's address on the Wi-Fi.

## Keeping data

Events older than `retention.events_days` (365) are deleted by a daily job in `reports`. Reports stay until the person deletes their account.

## Testing

- **Fast tests:** the API with the fake `Store`, `ReportWriter` and `GoogleLogin`, a contract suite for each, plus the filter tests.
- **`docker` tests:** the whole `compose.yaml` on real Postgres, with a real upload and a report from the fake LLM.
- **`e2e` tests:** the website in a browser with fake Google.
- **By hand, by the user:** a real Google login, a real report with their own key, and a real Pi linked once the refactor is done.
- **Must be tested:** repeat uploads, events arriving out of order, one person asking for another's Pi, a removed Pi's token, a batch with a picture field or to-do text in it, the server off while `desktalk` keeps running, a week with no data, a report that is written late, a Pi still catching up when a report comes due, "Link again" keeping the history, and a chat request's text appearing in no log or table, even when the AI company returns an error.
- Add `cloud/` to coverage, pyright, ruff, vulture and CI. `cloud/web` gets `npm test` and `npm run build`, like `dev_ui/`.

## Left to the builder

Library choices within these tools (SQLAlchemy or plain SQL, how the admin password is hashed), page layout, exact error wording, and numbers not given above, which go in `cloud/settings.yaml`.

## Other things to change

- `AGENTS.md`: add a short "Cloud server" section with the rules above.
- `README.md`: says the app works with no accounts. Add that the cloud server is optional.
- The Congressional App Challenge text says "no accounts and no cloud server", so update it if the submission is still open.
