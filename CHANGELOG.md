# Changes

What changed in Desk Talk, newest first, one line or a few per change. Each pull request on GitHub has the
full story: what was wrong, what changed, and the checks that were run. What a person tried on the real Pi is in
[docs/RUNBOOK.md](docs/RUNBOOK.md#checking-on-the-real-pi).

## 2026-11-27

- **The phone camera is gone.** The app takes its pictures from the Pi, or from a webcam when you work on it on a
  computer. The phone page (`phone_camera/`), its HTTPS server, certificate and QR code, the tilt check that told
  you the phone got bumped, and the `camera.phone_*` settings are removed, along with the `cryptography` and
  `qrcode` packages. An old `camera.phone_*` line in `settings.local.yaml` is left out with a log line, and
  `camera.source: phone` stops the start with a plain message. The phone on your desk (the phone-free habit) and
  notifications on your phone through ntfy work as before. The unused `docs/diagrams/` pictures and the finished
  desk-test plan in `benchmarks/` are gone too.

## 2026-11-21

- **A cover picture and a preview setting**, pushed straight to main. The Desk Talk cover (the page it was made from,
  and a PNG at two sizes) is in `reports/desk-talk-cover/`, and `.claude/launch.json` lets Claude Code start the app
  for a preview.

## 2026-11-15

- **Notifications on your phone, and no false camera warning on a computer**
  ([#25](https://github.com/randomnoobi/desk-talk/pull/25)). Set `NTFY_TOPIC` (a secret name, in `.env` or
  `raspberry_pi/.env`) and the warnings, reminders, Lens's camera problems and sips the camera saw also go to your
  phone through ntfy. Only ready-made words go, never an AI's: a to-do reminder only says a to-do is due, and
  lost-item alerts don't go, as an AI may write them. A send gives up after 10 seconds, and after a failure nothing is
  tried for a minute. On a computer with the Pi as the camera, Lens no longer warns that no pictures are coming in.

- **The developer page shows the Pi's own health** ([#24](https://github.com/randomnoobi/desk-talk/pull/24)). On a
  computer with the Pi as the camera, the System tab's Health checks and Pi cards (and the Storage tab's Pi card)
  now come from the Pi itself, at `dev.pi_address`, instead of the computer's copy, which gets no pictures. The rest
  of the tab is the computer's own, marked "(this computer)". The Pi's "disk free" row is now "picture folder free"
  (the camera's 300 MB folder in memory, not the SD card), and its amber limit is 100 MB instead of 1000.

- **On a computer, the app points to the Pi's own Desk Talk** ([#23](https://github.com/randomnoobi/desk-talk/pull/23)).
  With the Pi as the camera, the camera panel and the start-up lines used to show a command to start the Pi's camera
  by hand, but the Pi's camera sends its pictures to the Pi's own Desk Talk. They now link there instead, at
  `dev.pi_address` (set it to your Pi's name, like `desktalk-pi.local:5000`).

## 2026-11-14

- **A 6-hour run on the real Pi with the SD card nearly full passed**, written down straight on main
  ([854273e](https://github.com/randomnoobi/desk-talk/commit/854273e)): Desk Talk kept saving in its own space and no
  container restarted. The numbers are in [docs/RUNBOOK.md](docs/RUNBOOK.md#checking-on-the-real-pi).
- **The Pi's numbers on the developer page** ([#20](https://github.com/randomnoobi/desk-talk/pull/20)). A new Pi
  tab shows, live and for the last 30 minutes: processor use per core, speed and load; every temperature sensor;
  the fan's speed; memory and swap; disk space (Desk Talk's own space and the rest of the SD card), disk reads and
  writes; network and Wi-Fi signal; the busiest programs; and warnings for a weak power supply, heat and slowing
  down. The `app` container reads them from Linux on the Pi; the developer page on your computer asks the Pi
  (`dev.pi_address`), only while the tab is open.
- **The Pi's log on the developer page** ([#19](https://github.com/randomnoobi/desk-talk/pull/19)). A new Pi log
  tab shows each container's log as it's written, over `ssh pi`, only while the tab is open. It connects again by
  itself when the Pi reboots or the Wi-Fi drops, without losing or repeating lines.
- **Desk Talk's own space on the SD card** ([#22](https://github.com/randomnoobi/desk-talk/pull/22)). The data
  folder is now a fixed 8 GB space that nothing else on the Pi can fill, so history keeps being saved when the rest
  of the card is full. New health checks say when the rest of the card runs low (`card_rest`) and when nothing is
  being saved (`saving`), and Lens says them out loud.
- **The camera works again after a power cut** ([#21](https://github.com/randomnoobi/desk-talk/pull/21)). The Pi
  can number the camera's devices differently at each boot; the boot service now writes this boot's numbers before
  starting the containers, so the camera container may open the camera.

## 2026-11-12

- **Fixed wording for warnings** ([#18](https://github.com/randomnoobi/desk-talk/pull/18)). A switch for
  AI-worded reminders, fixed lines for the desk edge and repeat warnings, and a speaker card for Demo warnings.
- **The Pi keeps going through power cuts, full cards and wrong clocks**
  ([#17](https://github.com/randomnoobi/desk-talk/pull/17)). Starts on boot with systemd; logs kept in the journal
  (200 MB, 30 days) with scripts to pull them; retries with jitter and one shared circuit breaker; each health
  measure checked on its own, with a heartbeat from the Pi; SD card space limits and a weekly backup to the Mac;
  nothing deleted by date while the clock is wrong; a runbook and failure hand tests.

## 2026-11-11

- **Object states kept across an app restart with no internet**
  ([#16](https://github.com/randomnoobi/desk-talk/pull/16)).
- **Every object starts over when the Pi's clock is behind after a power cut**
  ([#15](https://github.com/randomnoobi/desk-talk/pull/15)).

## 2026-11-04

- **Everything on the Pi runs in Docker, by itself** ([#14](https://github.com/randomnoobi/desk-talk/pull/14)): no
  computer, Wi-Fi or AI needed.
- **An optional, self-hosted cloud server for weekly reports** ([#12](https://github.com/randomnoobi/desk-talk/pull/12)).
- **The full stack test reads its labels from the right place** ([#13](https://github.com/randomnoobi/desk-talk/pull/13)).
- **Sips need someone at the desk, a blocked-camera warning, a wider sensitivity slider, object stories and a Demo
  tab** ([#11](https://github.com/randomnoobi/desk-talk/pull/11)).

## 2026-10-26

- **No more false sips or phantom objects**; the object trace, Mac notifications, sensitivity and to-do due dates
  ([#10](https://github.com/randomnoobi/desk-talk/pull/10)).

## 2026-10-25

- **A developer page at `/dev/`** ([#9](https://github.com/randomnoobi/desk-talk/pull/9)): run the checks, watch
  object states, the Pi's health, the database and storage.
- **A test that pictures turn into object states, events and saved rows**
  ([#8](https://github.com/randomnoobi/desk-talk/pull/8)).
- **Code cleanup** ([#7](https://github.com/randomnoobi/desk-talk/pull/7)): dead parts deleted, numbers and wording
  moved into config.
- **Object states, you as the on/off switch, and the hydration rule** ([#6](https://github.com/randomnoobi/desk-talk/pull/6)).
- **Two kinds of false safety alerts stopped** ([#5](https://github.com/randomnoobi/desk-talk/pull/5)): drinks
  behind the laptop, and arms seen as cats.

## 2026-10-23

- **A rule engine for habit nudges** ([#4](https://github.com/randomnoobi/desk-talk/pull/4)).
- **The refactor around interfaces, with detection on the Pi in Docker**
  ([#3](https://github.com/randomnoobi/desk-talk/pull/3)).

## 2026-10-22

- **A desk test that picks three fast models** ([#2](https://github.com/randomnoobi/desk-talk/pull/2)) and **a YOLO
  benchmark for the Mac and the Pi** ([#1](https://github.com/randomnoobi/desk-talk/pull/1)).
