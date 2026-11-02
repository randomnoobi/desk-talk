# Camera program (the `camera` container on the Pi)

The camera program runs in the `camera` Docker container, one of the Pi's three (`raspberry_pi/compose.yaml`).
Inside it runs Debian's Python 3 (3.11 or newer) with apt's Picamera2, OpenCV and PyYAML. It takes pictures,
keeps them on the Pi for a while, asks the `detector` container what's in them, and sends each picture with what
was found to the `app` container, on the same Pi. Nothing here needs a computer.

```
camera → picture ─┬─▶ detector container (http://detector:8100) ──▶ what's in it
                  └─▶ app container (/api/pi/frame): picture + what was found, one upload per picture
```

Each picture goes to the app marked:

- `ok`: the detector found these things; the app uses them.
- `busy`: the detector was still working on an earlier picture. This one is only for the live view.
- `off`: the detector couldn't answer (not started yet, still loading its model, crashed, too slow).
  The reason goes with the picture, and no objects are found in it. There is no backup detector.

So the camera keeps its pace when detection is slow or the container is down.

## Set it up

You don't, by hand. `bash ~/desk-talk/raspberry_pi/install.sh` (see [raspberry_pi/README.md](../README.md)) builds the image,
gives the container the camera's devices and starts it with the other two. There are no apt packages to install on the
Pi, no service file and no `--server` or `--key` to type. The program only needs the standard library, the three apt
packages in the image, and `shared/` from the repo, and it never imports `desktalk/`.

## Run it

It starts by itself, with the Pi, and starts again when it stops. **On the Pi**, in `~/desk-talk/raspberry_pi`:

```bash
docker compose ps
```

```bash
docker compose logs -f camera
```

The log (Ctrl-C stops watching) says "Connected" once the app takes its pictures. The container counts as
`healthy` when it saved a picture in the last couple of minutes.

To check everything, with the camera container stopped first (only one program can hold the camera), **on the Pi**:

```bash
docker compose stop camera
docker compose run --rm camera python3 -m raspberry_pi.camera --self-test
docker compose up -d
```

The self-test checks the camera, the detector, the Pi's temperature and that the app answers, one line each, and says what
to do about any problem. It sends no pictures. (This form of the command hasn't been tried on a real Pi yet.)

`python3 -m raspberry_pi.camera --help` lists every flag, but you rarely need them: the container gets what it needs
from its environment.

## Settings

The camera program asks the app for its settings (`config/settings.yaml`, the `pi:` section, with
`raspberry_pi/settings.pi.yaml` and then your own `raspberry_pi/mine/settings.mine.yaml` laid over it) when it starts, and again every `pi.settings_check_every_sec`. New
`pi.detector` settings are passed to the detector right away; for anything else, restart the camera
(`docker compose restart camera`). If the app can't be reached at the start, it uses the copy of `config/settings.yaml`
inside its image. Flags win over both:

| Flag | What it does |
|---|---|
| `--camera` | `auto`, `picamera2` (camera module), `usb` / `usb:1` (USB webcam), `folder:<dir>` or `video:<file>` for testing. Without it: `pi.camera` in the settings (`auto`) |
| `--fps` | pictures per second |
| `--photo-dir` | where the pictures are saved (the container's `/photos` volume) |
| `--delete-every-min` | delete the saved pictures this often (0 = never) |
| `--keep-last-min` | minutes of pictures a cleanup keeps (0 = delete them all) |
| `--min-free-mb` | if the disk gets this full, delete the oldest pictures right away |
| `--no-detector` | don't use the detector; pictures go to the app with no objects found |
| `--offline` | only take and save pictures; send nothing |
| `--self-test` | check everything, then stop |

## Where it sends, and how it starts

The container's own numbers are in `raspberry_pi/compose.yaml` and `raspberry_pi/.env`: `restart: unless-stopped` starts
it again when the Pi boots and whenever the program stops (including when the watchdog stops it). It is told where the app
is, and where the pairing key is, by its environment instead of `--server` and `--key`:

- `DESKTALK_SERVER`: the app, `http://app:5000` (the other container's name, on Docker's own network).
- `DESKTALK_PAIR_KEY_FILE`: the path, inside the container, of a file with the pairing key in it (or use
  `--key-file <path>`). The app writes the key into the `pairing` volume and the camera reads it from there, so it is
  never in `docker ps` or a log.

A flag always beats the environment, and `--key` beats the key file. Only this container gets the camera's devices, and
only the kinds of device named in the compose file; it runs with no extra rights.

## If something's wrong

The log (`docker compose logs --tail 50 camera`, **on the Pi**, in `~/desk-talk/raspberry_pi`) says what's wrong in plain words.

- **"Cannot reach the app"**: is the `app` container up? `docker compose ps`. The camera keeps taking pictures meanwhile and
  tries again after a short wait that grows each time (up to `pi.upload_retry_max_sec`).
- **"Pairing key does not match"**: the app made a new key. Run `docker compose up -d` again, or `docker compose restart camera`.
- **"camera.source: phone"**: the app is set to take its pictures from a phone or webcam. On the Pi it shouldn't be:
  `raspberry_pi/settings.pi.yaml` leaves it at `pi`, so look for a `camera:` line in `raspberry_pi/mine/settings.mine.yaml`.
- **"Stopped asking the detector"**: the detector isn't answering. `docker compose ps` and `docker compose logs detector`.
  The camera checks again by itself and goes back to using it once it's ready. The detector has no open port, so
  `curl http://127.0.0.1:8100` on the Pi doesn't answer; the detector's [README](../detector_service/README.md) shows how
  to ask it.
- **"The detector has no model yet"**: the first time a model is used, the detector converts it, which takes
  a few minutes. No objects are found until it's ready.
- **Camera problem**: `docker compose stop camera`, then `rpicam-hello` should show a picture; check the ribbon cable. If
  `rpicam-hello` works but this container can't open the camera, the device numbers in `.env` may have changed with a kernel
  update: `bash ~/desk-talk/raspberry_pi/update.sh` fills them in again. For a USB webcam use `pi.camera: usb`.
- **"No picture from the camera ... it seems stuck"**: the camera stopped giving pictures for
  `pi.restart_after_missed_pictures` picture times, so the camera program stopped and Docker started the container again
  (`restart: unless-stopped`). If it keeps happening, check the ribbon cable or the USB plug.
- **The temperature line can't tell if the Pi slowed itself down**: `vcgencmd` doesn't work inside a container, and a Pi 5's
  kernel only says whether the power supply is too weak. **On the Pi itself**, `vcgencmd get_throttled` shows the flags.
