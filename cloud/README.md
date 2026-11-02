# The Desk Talk cloud server

It keeps what your Pis may send (never pictures, never to-dos), and writes each Pi a weekly report you read on a website. You run it yourself, with Docker.

**It's plain HTTP on your home network for now.** There's no HTTPS, so keep it on the home network and don't open its port to the internet. Passwords, tokens and reports travel unencrypted between your devices and the server.

## 1. Fill in `.env`

```
cd cloud
cp .env.example .env
```

| In `.env` | What it is | Needed? |
|---|---|---|
| `SESSION_SECRET` | Signs the login cookie. A long random text. | Yes, it won't start without it |
| `POSTGRES_PASSWORD` | The database's password, letters and digits only. | Yes, it won't start without it |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Log in with Google (step 2). | Yes in practice: it starts without them, but **nobody can log in** |
| `PUBLIC_URL` | The address **a Pi** uses to reach this server. Usually this computer's home network number, like `http://192.168.1.20:8080`. It appears in the setup command. | Yes, if the Pi is another machine. Empty means `http://localhost:8080` |
| `LOGIN_URL` | The address **you** open the website at, and where Google sends you back to. Google only accepts `localhost` or a real domain, not a home network number: `http://localhost:8080`. Empty means `PUBLIC_URL`. | Yes, when `PUBLIC_URL` is a number |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | The admin page: who's on the server, failed reports, "run again". | Optional |
| `REPORT_API_KEY` | Your key for the AI company that writes the reports (`reports.provider` and `reports.model` in `settings.yaml`). | Optional: with none, reports are marked failed and say why |
| `CHAT_API_KEY` | Your key for the chat a Pi may use through this server (`chat.url` in `settings.yaml`). | Optional: empty means chat is off |

`.env.example` shows how to make the two random values. Put a value in single quotes if it has a `$` in it.

Open the website at the `LOGIN_URL` address. The login cookie belongs to one address, so logging in from a different one sends you to that address first.

## 2. Make a Google client ID

1. Go to [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials) and pick or make a project. If it asks, set up the consent screen (type "External") and add your own email as a test user.
2. Choose **Create credentials**, then **OAuth client ID**, then **Web application**.
3. Under **Authorised redirect URIs** add your `LOGIN_URL` followed by `/auth/google/callback`, for example `http://localhost:8080/auth/google/callback`.
4. Copy the client ID and secret into `.env`.

## 3. Start it

```
docker compose up -d --build --wait
```

`--wait` makes it wait until everything is healthy, and it says so if something doesn't get there. It comes back by itself when the computer boots (as long as Docker starts on boot). The website is at `http://localhost:8080`. Only the website's port is open: the database and the server behind it can't be reached from outside.

To use a different port, set `WEB_PORT` in `.env`, and put the same port in `PUBLIC_URL` and `LOGIN_URL` (and in the address you gave Google in step 2). Left empty, those two still say `8080`, so the setup command and the login would go to the wrong place.

`docker compose logs -f` shows what it's doing. If it stops because of a setting, the log says which one in plain words.

Settings in `settings.yaml` and wording in `prompts.yaml` are built into the image: after changing one, run the same command again. If you change `ingest.max_body_kb` or `server.port`, change `web/nginx.conf` to match (a test compares them).

## 4. Add a Pi

1. Log in on the website and click **Add a Pi**.
2. Run the command it shows, **on the Pi**, from any folder. It gets Desk Talk if the Pi doesn't have it, installs it, and uses the code first. The code in it works once, for a few minutes.
3. The Pi appears on the website. Its events show up as it sends them.

## If linking says "Too many tries"

The server counts tries per address: 10 in 10 minutes (`limits.claim_tries` and `limits.claim_window_sec` in `settings.yaml`), right or wrong, to use a code. Past that, that address waits until its oldest tries are 10 minutes old. Admin logins are counted the same way, on their own.

On Colima or Docker Desktop, every device on your home network may reach the server as the same address, so 10 tries from anyone can block linking for everyone for up to 10 minutes. To spot it:

```
docker compose logs web
```

Each request's line starts with the address the server saw. If your Pi and your other devices all show one address, like `172.30.87.1`, they share one count. Restarting `api` clears the count (`docker compose restart api`), because it's kept in memory.

## Your own API keys

Reports go to Anthropic by default. To use Gemini, OpenAI, Groq or Ollama, set `reports.provider: openai_compatible`, `reports.url` and `reports.model` in `settings.yaml`. A keyless Ollama still needs some text in `REPORT_API_KEY`. Your key stays on this server. Each report sends the AI company that Pi's events for the week.

Keep exactly one `reports` container. Two would both pay for the same report.

## Changing the admin password

The password is saved the first time a username is used, and after that the one in `.env` is ignored for that username (the log says so). So changing the password needs a **new username**: set a new `ADMIN_USERNAME` and `ADMIN_PASSWORD`, then run `docker compose up -d`. The old username stops working.

## Stopping, and backing up

```
docker compose stop                   # stop it for now
docker compose down                   # stop it and remove the containers; the data stays
docker compose down --volumes         # also DELETES ALL THE DATA
```

Your data is in a Docker volume. To back it up, with the server running:

```
docker compose exec -T postgres pg_dump -U desktalk -d desktalk > desktalk-backup.sql
```

To put a backup back, start from an empty database, and start only Postgres until it's in:

```
docker compose down --volumes
docker compose up -d --wait postgres
docker compose exec -T postgres psql -U desktalk -d desktalk < desktalk-backup.sql
docker compose up -d
```

Keep backups private: they hold everything your Pis sent. `POSTGRES_PASSWORD` is set the first time the database starts, so changing it in `.env` later stops the server from connecting.

## If the network clashes

The containers use the network `172.30.87.0/24`. If you already use it, set `NETWORK_SUBNET`, `NETWORK_ADDRESSES` and `WEB_CONTAINER_ADDRESS` in `.env` (`.env.example` explains them), then run `docker compose down` and start it again.
