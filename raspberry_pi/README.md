# Raspberry Pi

The Pi runs all of Desk Talk by itself: it is the camera, it finds the objects in its own pictures, and it serves
the app's page. Three Docker containers from one compose file (`raspberry_pi/compose.yaml`) do it, and nothing runs
outside Docker. No computer, no AI and no account is needed.

```
Raspberry Pi (docker compose)
 camera                      detector                       app
 ──────                      ────────                       ───
 3 pictures a second,   ───▶ YOLO: what is in this picture? ──┐
 kept on the Pi,        ◀─── (no open port: only camera       │
 deleted every 10 min        and app can reach it)            ▼
        │                                          events, characters, habits,
        └── picture + what was found, one upload ─▶ warnings on the speaker,
            for each picture                        the page on port 5000
                                                        ▲
                       http://desktalk.local:5000, from a phone or computer on your Wi-Fi
```

- **`camera`** takes **3 pictures a second**, keeps them in the Pi's memory (not on the SD card) and **deletes them
  every 10 minutes**, asks the detector what's in each one, and sends each picture with what was found to the app.
  Only this container gets the camera's devices.
- **`detector`** runs YOLO. If it is still loading, or down, the camera sends the pictures anyway, with nothing
  found, and Lens says the detector is off. Docker starts it again. There is no other detector to fall back on.
- **`app`** is the same Desk Talk code that runs on a computer. It is the only container with an open port, and it
  writes everything to the `data/` folder.

Pictures never leave the Pi, except to a page someone opens on your own Wi-Fi. The app has no login on the Wi-Fi:
anyone who can join your Wi-Fi can open the page, and only the Wi-Fi's password keeps them out.

> **What has and hasn't been tried on a real Pi.** Tried on a Pi 5 (8 GB): `install.sh` from a fresh clone at
> `~/desk-talk` with Docker emptied first (exit 0 in about 9 minutes, including the model), running it again (4 seconds,
> the pairing key kept), `update.sh` (5 seconds and nothing restarted when there is no new code; all three containers
> rebuilt and made again with a new commit), all three containers coming back by themselves after `sudo reboot`, the
> three containers together with the real camera, pictures flowing into events, the speak command with a stand-in for
> the speaker (so nobody has heard it), and that no picture leaves the Pi. `DESKTALK_PI=pi uv run pytest -m pi` gave 7
> passed and 5 skipped; the skipped ones need a person. A pulled plug was tried too (2026-11-10): all three came back
> by themselves, the files stayed readable, and saved states started over until the Pi's clock had synced.
> **Not tried yet:** `install.sh` on a blank card (this card already had Docker, the lines in the boot files for the
> memory limits and the camera, and the new name, put in by hand, so it skipped those), a real USB speaker, a real
> object on the desk, a start with no Wi-Fi, no network at all, a real phone, a real cloud link, and
> closing the laptop an AI runs on. They are the steps in [docs/PI_CHECKS.md](../docs/PI_CHECKS.md).

More detail: the camera's [README](camera/README.md) and the detector's [README](detector_service/README.md).

## What you need

- A Raspberry Pi with the camera module (or a USB webcam). Only a Pi 5 with 8 GB has been tried. The install stops on
  a Pi with under about 2 GB of memory, and says to lower `DETECTOR_MEMORY_LIMIT` below about 3.5 GB.
- **64-bit** Raspberry Pi OS, with the Pi on your Wi-Fi.
- **A 32 GB SD card or larger.** The first install needs about 14 GB free: the three images and Docker's saved build
  steps take about 12.6 GB. A 16 GB card is too small, and the install stops and says so.
- A USB speaker, if you want to hear warnings and reminders. Nothing else needs it.

## Install with one command

**On the Pi** (Raspberry Pi OS 64-bit):

```bash
git clone https://github.com/randomnoobi/desk-talk ~/desk-talk
bash ~/desk-talk/raspberry_pi/install.sh
```

It checks the Pi (64-bit, free disk, memory, sound), installs Docker if it's missing, makes `raspberry_pi/.env` from
`.env.example` with this Pi's own numbers, fetches the model and starts the three containers. The first time takes a
while: it builds the images, which download PyTorch. A Pi still called `raspberrypi` is renamed `desktalk`; a Pi you
named keeps its name. It's safe to run again, and never touches the data folder, the pairing key or the models. It says
if the Pi needs a restart (for the memory limits and the camera, which need a line in the Pi's boot settings), and only
does that by itself with `--reboot`. At the end it prints the address to open.

It replaces the old steps by hand: apt packages, a systemd service for the camera, and starting the camera with
`--server` and `--key`. None of those are needed any more.

| Flag | What it does |
|---|---|
| `--tz Europe/Paris` | your time zone, if the Pi's isn't right |
| `--server <address>` with `DESKTALK_LINK_CODE=<code>` in front | link to your cloud server first, as its website's "Add a Pi" box says. The Pi needs no cloud: if this fails, the rest still installs, it says so with the command to try again, and exits with 3 |
| `--code <code>` | the code as a flag, for typing by hand. Others on the Pi can see it in the list of running programs while this runs |
| `--keep-hostname` | keep the name even if it's still `raspberrypi`, and remember that |
| `--reboot` | restart the Pi at the end, if it needs that |
| `--dry-run` | do the same checks, say what it would do, and change nothing |

**Renaming the Pi changes its address**, to `http://desktalk.local:5000`. It says so at the end; change the old name
anywhere you saved it, such as your Mac's `~/.ssh/config`. The links the app shows come from the Pi's real name and
`APP_HOST_PORT`, which it writes to `.env` as `PUBLIC_NAME`. If that name doesn't work on a phone, use the Pi's number:
**on the Pi**, `hostname -I`.

To link to the cloud server later, **on the Pi**, run the install command again with the website's code (the same
command the website's "Add a Pi" box shows):

```bash
DESKTALK_LINK_CODE=<the code> bash ~/desk-talk/raspberry_pi/install.sh --server <the server's address>
```

`raspberry_pi/.env` is yours and isn't in git. New settings from `.env.example` get added to it by the install and
update commands, and what's already in it stays. The model's checksum is in `raspberry_pi/model_checksums.txt`: a
model that isn't listed is still used, and the script says it couldn't check it.

## Is it working?

**On the Pi**, in the compose folder. After a minute or two all three should say `healthy`:

```bash
cd ~/desk-talk/raspberry_pi && docker compose ps
```

Then open **http://desktalk.local:5000** on a phone or computer on the same Wi-Fi. Within a few seconds the Desk cam
panel shows the live picture. To see what each one is doing (Ctrl-C stops watching):

```bash
docker compose logs -f camera
```

Use `app` or `detector` in place of `camera` for the others. The app also writes `data/logs/desktalk.log`.

### Starting by itself

The install sets up `desktalk.service` (from `raspberry_pi/systemd/desktalk.service`), which starts the three
containers each time the Pi boots, after a power cut too, with or without Wi-Fi. It runs as your own user, who is in the
docker group, so it needs no root, and it never builds: it only starts what the install or the update built. Docker's own
rule (`restart: unless-stopped`) brings them back as well. First, though, it writes this boot's numbers for the camera's
devices into `.env` (`raspberry_pi/device_numbers.sh`): the kernel can number them differently at any boot, and the
camera container may open only the numbers in `.env`, so after a boot that changed them only this brings the camera
back. To check it, **on the Pi**:

```bash
systemctl status desktalk          # "active (exited)" means it started them; it has nothing more to do until the next boot
journalctl -u desktalk -b          # what it said at this boot
```

Stopping `desktalk.service` leaves the containers running. To stop them, use `docker compose stop` in
`~/desk-talk/raspberry_pi`. They start again at the next boot.

### Where the logs are

All three log to the Pi's journal, with the Pi's own messages. Raspberry Pi OS keeps the journal in memory only, so it
would be gone after each restart; the install changes that (`raspberry_pi/systemd/journald-desktalk.conf`), so it stays
on the SD card through a power cut, uses at most 200 MB (`JOURNAL_MAX_USE` in `.env`) and keeps 30 days
(`JOURNAL_KEEP`). **On the Pi**:

```bash
docker compose logs -f camera                      # in ~/desk-talk/raspberry_pi, as before
journalctl -t desktalk-camera --since "2 hours ago" # the same, from the journal, with containers a rebuild replaced too
journalctl -b -1                                   # everything from the boot before this one, such as before a power cut
journalctl --list-boots                            # the boots it has
journalctl --disk-usage                            # how much room it takes
```

### Getting the logs onto your Mac

**On the Pi**, `bash ~/desk-talk/raspberry_pi/get_logs.sh 6` saves the last 6 hours (24 when no number is given) of each
container's log, the list of boots, `docker compose ps`, the disk and the temperature, all in one dated `.tar.gz` in
`~/desk-talk/pi_logs/`, and prints its path. It keeps the newest 10 there.

**On your Mac**, in the `desk-talk` folder, this does the same over ssh and copies the file into `pi_logs/` here:

```bash
bash scripts/pi_logs.sh                       # the Pi you reach with `ssh pi`, the last 24 hours
bash scripts/pi_logs.sh --host desktalk.local --hours 6
tar -xzf pi_logs/desktalk-logs-*.tar.gz -C pi_logs     # to read it
```

Each number is in the logs part of `raspberry_pi/.env.example`.

The detector has no open port, so `curl http://127.0.0.1:8100` on the Pi doesn't answer; that is on purpose. Ask it from
inside its own container instead:

```bash
docker compose exec detector python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8100/health').read().decode())"
```

It says `starting` until the app's camera tells it which model to run, `loading` while it converts it, then `ready`.
Or read what the Pi has said lately from the app (**on the Pi**; from a computer use the Pi's address in place of
`localhost`):

```bash
curl http://localhost:5000/api/health
```

Its `pi` part has what the camera said lately: the detector's state and why, how long detection takes and the model.

## Settings

The Pi's own settings are in `raspberry_pi/settings.pi.yaml`, laid over `config/settings.yaml`. Both the app and the
camera read it (compose mounts it into both as `config/settings.local.yaml`, read-only). It comes with the code and is
in git, so **don't edit it**: `update.sh`'s `git pull` would stop on it.

Your own changes go in `raspberry_pi/mine/settings.mine.yaml`, laid over both. `install.sh` makes it the first time, from
`settings.mine.example.yaml`, which has only comments and two examples. It is not in git, `install.sh` and `update.sh`
never change it once it's there, and the Pi works with it empty or gone. Put just the lines you change in it, in the
same layout as `config/settings.yaml` (for example `llm:` then `  ai_url: ...`). After a change, **on the Pi**:

```bash
cd ~/desk-talk/raspberry_pi && docker compose restart app camera
```

The camera asks the app for its `pi:` settings when it starts, and again every minute (`pi.settings_check_every_sec`).
New `pi.detector` settings (model, picture size ...) go to the detector right away; for anything else, restart the
camera. If the app can't be reached when it starts, the camera uses the copy of `config/settings.yaml` inside its image.

Two `pi:` settings you may want: `pi.camera` picks the camera (`auto`, `picamera2`, `usb`, `usb:1` ...), and
`pi.error_text_chars` is how much of a reply the camera can't read goes in its log (200 letters).

The containers' own numbers (ports, memory limits, how often Docker checks them, how big their log files get, and the
install command's limits) are in `raspberry_pi/.env`; the detector's [README](detector_service/README.md) lists its
ones. After changing one, run `docker compose up -d` again, **on the Pi**, in `~/desk-talk/raspberry_pi`. `DETECTOR_PORT` must
match `pi.detector.url` in `settings.pi.yaml`, and `APP_PORT` must be `web.port`.

### The speaker, and chat

Warnings and reminders are said out loud through a USB speaker (`notifications.output` and `notifications.sound_card` in
`raspberry_pi/settings.pi.yaml`; to change them, put the lines in `raspberry_pi/mine/settings.mine.yaml`). Plug it in whenever you like: no restart needed. With none plugged in, the log says so
once and the Pi carries on. Quiet hours (the app's Settings page) mute it. A webcam with a microphone has `usb` in its
name too, and can get picked ahead of the speaker: then set `sound_card` to a word only the speaker's name has
(**on the Pi**, `cat /proc/asound/cards` lists them). Nobody has heard it on a real USB speaker yet.

Chat with the characters is optional and needs an AI somewhere else, since none runs on the Pi. Set `llm.ai_url` in that
same file (`config/settings.yaml` says how), then `docker compose restart app`:

- **Ollama on your own computer**: `http://<your computer>:11434/v1`, plus `ai_on_my_computer: true` under `llm:`.
  Text only goes there: your chat messages, and the words the characters say by themselves (reminders, small talk,
  the recap), which can include the text of your to-dos. Without `ai_on_my_computer: true` it gets only chat you start
  yourself. A reminder waits at most `llm.reminder_wait_sec` for it, then uses its ready-made words, and warnings
  never wait for it at all.
- **Your cloud server**: `<its address>/v1`, once the Pi is linked to it. Only chat you start yourself goes there,
  as text, and the server doesn't store or log it.

Pictures never go to either: `llm.send_pictures` is `false` on the Pi.

## If something's wrong

Each container's log (`docker compose logs --tail 50 camera`, **on the Pi**, in the compose folder) says what's
wrong in plain words.

### The detector container is down

Lens says the detector is off, and the camera's log says "Stopped asking the detector". Nothing breaks: the
camera and the app go on, but no objects are found until it's back. The camera checks again by itself and goes
back to it once it's ready.

**On the Pi**, see whether it's running, and what it said last:

```bash
cd ~/desk-talk/raspberry_pi && docker compose ps
```

```bash
cd ~/desk-talk/raspberry_pi && docker compose logs --tail 50 detector
```

**On the Pi**, start it again:

```bash
cd ~/desk-talk/raspberry_pi && docker compose up -d
```

- **`/health` says `error`**: the message says why. Often the model name is wrong, or the model file isn't in the
  detector's `models` volume yet: it never downloads anything itself (see the detector's [README](detector_service/README.md)).
  It also says `error` when a model is loaded but the last picture failed; it goes back to `ready` by itself when one
  works. It tries again by itself, waiting longer each time.
- **It keeps restarting** (`docker compose ps` shows "Restarting"): it may be running out of memory. Try a
  smaller model or picture size, or raise `DETECTOR_MEMORY_LIMIT` in `.env` if the Pi has memory to spare.
- **`docker compose up` warns that the memory limit isn't supported**: the install command adds
  `cgroup_enable=memory cgroup_memory=1` to the Pi's boot settings and says the Pi needs a restart. If you haven't
  restarted since, **on the Pi** run `sudo reboot`.
- **"The detector has no model yet"**: the container is still converting the model (a few minutes the first time),
  or the model isn't on the Pi. Run `bash ~/desk-talk/raspberry_pi/install.sh` again: it fetches the model named in
  `pi.detector.model` if it's missing.
- **"speaks version ... of its API"**: the containers are from different versions of the code. Run
  `bash ~/desk-talk/raspberry_pi/update.sh`, which rebuilds all three.
- A container that stops answering (not just failing) is started again by Docker after its health check has failed for
  a few minutes (the numbers are in `.env`). The detector gets 5 minutes, because converting a model can keep it busy.

### The Pi is too hot

Lens says the Pi is running hot (from `pi.hot_warning_c`, 75 °C; the Pi slows itself down at about 80 °C),
or that it's slowing itself down. Detection gets slower, so more pictures go out as `busy`.

The camera container can read the temperature, but not all the slow-down flags: `vcgencmd` doesn't work inside a
container, and a Pi 5's kernel only gives whether the power supply is too weak. So the Pi can't always tell whether it
slowed itself down. **On the Pi itself** (not inside a container), the temperature, and the slow-down flags
(`throttled=0x0` means fine):

```bash
vcgencmd measure_temp
```

```bash
vcgencmd get_throttled
```

What helps: a cooler or a small fan, a case with air holes, or less work for the Pi: a smaller model or
picture size (`pi.detector.model`, `pi.detector.image_size`) or fewer pictures a second (`pi.fps`). If Lens says the
power supply is too weak, use the official Raspberry Pi power supply.

### The page won't open

- Is the Pi on the Wi-Fi? From a computer on the same Wi-Fi, `ssh <the Pi>` should answer. A phone on mobile data can't
  reach it.
- `desktalk.local` needs your phone or computer to understand `.local` names. Try the Pi's number instead
  (**on the Pi**, `hostname -I`), as `http://<number>:5000`.
- **On the Pi**, `docker compose ps` should show `app` as `healthy`. If it isn't, `docker compose logs --tail 50 app`.

### No Wi-Fi at all

The Pi keeps going: it takes pictures, finds objects, saves events and says warnings on the speaker. The cloud server
(if linked) gets its events later, and an AI on another computer can't be reached, so chat says so. Nobody can open the
page until the Wi-Fi is back. Without a network, the Pi's clock can be wrong after a power cut (it has no clock
battery) until the network is back, so objects start over instead of picking up where they were. Starting with no Wi-Fi hasn't been tried on a real Pi yet
([docs/PI_CHECKS.md](../docs/PI_CHECKS.md)).

### Camera problem

The camera module is only for one program at a time. **On the Pi**, stop the camera container before testing it:

```bash
cd ~/desk-talk/raspberry_pi && docker compose stop camera
```

then `rpicam-hello` should show a picture (check the ribbon cable if not), and `docker compose up -d` starts the camera
again. If `rpicam-hello` works but the camera container says it can't open the camera ("Operation not permitted" on
`/dev/media*`), the numbers for the camera's devices in `.env` (`CAMERA_MEDIA_MAJOR`, `CAMERA_DMA_HEAP_MAJOR`) may not
be this boot's. The kernel can number them differently at any boot, and `desktalk.service` writes them again each time
before it starts the containers. To do it by hand, **on the Pi**:

```bash
cd ~/desk-talk/raspberry_pi && bash device_numbers.sh && docker compose up -d
```

`journalctl -u desktalk -b` shows what it found at this boot. For a USB webcam set `pi.camera: usb` in
`raspberry_pi/mine/settings.mine.yaml`.

"No picture from the camera ... it seems stuck": the camera stopped giving pictures for `pi.restart_after_missed_pictures`
picture times, so the camera program stopped and Docker started the container again. If it keeps happening, check the
ribbon cable or the USB plug.

### Other problems

- **"Pairing key does not match"** in the camera's log: the app made a new key (its `pairing` volume was removed). The
  camera reads the key when it starts, so run `docker compose up -d` again, or `docker compose restart camera`.
- **The old camera service is still running** (the install command says so): the old setup holds the camera. Stop it with
  `sudo systemctl disable --now desk-talk-camera`.
- **Lens says the Pi's clock is off**: **on the Pi**, `timedatectl` should say `System clock synchronized: yes`.

## Updating

**On the Pi**:

```bash
bash ~/desk-talk/raspberry_pi/update.sh
```

It runs `git pull`, fills in the camera's device numbers again, since a kernel update can change them, rebuilds, and
clears old images. Then it cuts Docker's saved build steps down to `INSTALL_KEEP_BUILD_STEPS_MB` in `.env` (20 GB), the
ones not used for longest first, so they can't take over the card over many updates. A whole build's steps take about
13 GB and are kept, since a whole new build takes 12 to 21 minutes; if the card runs low they are all cleared. (Docker
28 and newer call that `--max-used-space`, older ones `--keep-storage`; if neither works it says so, with how much room
they take.) It needs no sudo on a Pi this version's `install.sh` set up. On a Pi an older `install.sh` set up, it asks
for sudo once: to put in the journal file or `desktalk.service` if they're missing, since the containers log to the
journal and without that file their logs would be lost at every restart, and to put in the new `desktalk.service` if
this version changed it (it now writes this boot's device numbers for the camera first, so a power cut can't leave the
camera container unable to open the camera). Then it restarts `desktalk.service`. If sudo doesn't work there, it says
what that leaves undone and to run `install.sh` once. It takes
`--dry-run` too. Your data, the pairing key, the models, `.env` and your own settings (`raspberry_pi/mine/settings.mine.yaml`) stay. If `git pull` couldn't run
(no internet, or a file you changed by hand), it says so and rebuilds with the code that's there.

All three containers are rebuilt together, so they are always the same version.

### Coming from the older setup

If you ran the camera program as a systemd service and the detector from `raspberry_pi/detector_service`, run the install
command once. It stops the old `desktalk-detector` containers and keeps their models, and it tells you to stop the old
camera service (`sudo systemctl disable --now desk-talk-camera`), which would otherwise hold the camera. The computer that
ran `server.py` is no longer needed.

If that computer wrote its pairing key into `data/logs/` (older versions did), see
[README.md](../README.md#on-a-computer-for-working-on-it) for the command that clears those lines and makes a new key.

## Backups

A backup copies the Pi's data folder (the event log, habits, to-dos, desk rules, story pictures and the object state
database) and its own settings (`raspberry_pi/.env` and `raspberry_pi/mine/`) to your Mac. It stays on your Mac. The
logs aren't copied unless you add `--with-logs`.

**Back up now**, **on your Mac**, from the repo folder (the app must be running on the Pi):

```bash
bash scripts/backup_pi.sh
```

It reaches the Pi with `ssh pi` (a key login, never a password), saves into `~/DeskTalkBackups/<day>-<time>/`, keeps the
newest 8 backups and says what it saved. `--host`, `--to` and `--keep` change those; the top of the script lists them all.
The database is copied with SQLite's own backup inside the app container, so the copy is whole even while the app writes.

**Every week**, **on your Mac**:

```bash
bash scripts/install_weekly_backup.sh                                # every Sunday at 10:00
bash scripts/install_weekly_backup.sh --day wednesday --time 21:30
bash scripts/install_weekly_backup.sh --uninstall                    # stop it
```

It runs while you're logged in; a Mac that was asleep then runs it when it wakes. What each run printed goes to
`~/DeskTalkBackups/backup.log`. To try it at once: `launchctl kickstart gui/$(id -u)/local.desktalk.weekly-backup`.

**Restore** a backup (on a new card, run the install command first):

1. **On the Pi**, stop the containers and remove the database's side files, which belong to the database being replaced:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose stop
   rm -f ../data/object_states.sqlite-wal ../data/object_states.sqlite-shm
   ```

2. **On your Mac**, copy the backup back over (pick the folder you want):

   ```bash
   rsync -a ~/DeskTalkBackups/2026-11-10-100000/ pi:desk-talk/
   ```

3. **On the Pi**, start them again: `cd ~/desk-talk/raspberry_pi && docker compose up -d`.

### What keeps the SD card from filling up

The camera's pictures are kept in memory, not on the card. The app's log files have a size limit (`logging` in
`config/settings.yaml`), the event log keeps 30 days (`event_log.keep_days`), the story pictures a set number per
object (`object_story`), and the object state database 30 days (`object_states.keep_days`). Docker's saved build
steps are cut down after each update (see [Updating](#updating)).

## Choosing the model

The Pi runs `yolo26s-objv1-150.pt` at 640 pixels (`pi.detector` in `raspberry_pi/settings.pi.yaml`). It knows the
same 365 kinds of objects as the computer's model, so the words in `config/agents.yaml` mean the same here. Measured
on a Pi 5 (8 GB) with all three containers running, it looked at all 3 pictures a second, 200 to 215 ms each, using about
2 of the 4 cores, and stayed under 62 °C with the fan. The bigger `yolo26m-objv1-150.pt` at 640 managed only 1.5 a second,
and at 480 2.4 a second. The numbers are in [the first-run notes](../docs/plans/2026-10-30-pi-first-run-notes.md).

To try another, the speed test in [`benchmarks/`](../benchmarks/README.md) helps:

1. Run the benchmark on the Pi, in its own Docker image. The steps, each labelled **on the Pi** or
   **on your Mac**, are in [benchmarks/README.md](../benchmarks/README.md); the full run takes a few hours.
2. **On your Mac**, fetch the results and rebuild the summary (also in that README). The **best pick** in
   [benchmarks/results/README.md](../benchmarks/results/README.md) is the fastest combination that keeps up
   with 3 pictures a second and reaches at least 90% of the best accuracy.
3. Set it **on the Pi**, in `raspberry_pi/mine/settings.mine.yaml`, under `pi:` then `detector:`:

   | Benchmark name, e.g. `yolo11s-480-ncnn-fp16` | Setting |
   |---|---|
   | `yolo11s` | `pi.detector.model: yolo11s.pt` |
   | `480` | `pi.detector.image_size: 480` |
   | `ncnn` | `pi.detector.format: ncnn` |
   | `fp16` / `fp32` | `pi.detector.half: true` / `false` |

   Then run `bash ~/desk-talk/raspberry_pi/install.sh` (it fetches the model if it isn't on the Pi) and
   `docker compose restart app camera`. The camera passes the new settings to the detector, which converts the model
   (a few minutes the first time; no objects are found meanwhile).

Things to know when you pick:

- The detector container runs only the **`ncnn`** and **`pt`** formats. The benchmark also tries `onnx`, but
  an `onnx` pick can't be used there; take the best `ncnn` (or `pt`) one.
- The `yolo11` models only know the 80 everyday COCO objects: no keys, earphones or pens, which the lost
  items and some characters need. To try models that know those (the Objects365 ones, like the Pi's),
  add them to `config/benchmark.yaml` and run the benchmark again.
- A model file of your own (not one Ultralytics can download) has to be copied into the detector's
  `models` volume: see the detector's [README](detector_service/README.md).
