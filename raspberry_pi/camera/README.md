# Camera program (on the Pi)

The camera program runs on the Pi's own Python 3 (3.11 or newer), not in Docker. It takes pictures,
keeps them on the Pi for a while, asks the detector container on the same Pi what's in them, and sends
each picture with what was found to your computer.

```
camera → picture ─┬─▶ detector container (127.0.0.1:8100) ──▶ what's in it
                  └─▶ your computer (/api/pi/frame): picture + what was found, one upload per picture
```

Each picture goes to your computer marked:

- `ok`: the Pi's detector found these things; the computer uses them.
- `busy`: the detector was still working on an earlier picture. This one is only for the live view.
- `off`: the detector couldn't answer (not started yet, still loading its model, crashed, too slow).
  The reason goes with the picture, and the computer finds the objects itself.

So the camera keeps its pace when detection is slow or the container is down.

## Set it up (once)

1. **On the Pi**: Raspberry Pi OS already has Python 3, Picamera2 and PyYAML. Make sure, and get git and OpenCV:

   ```bash
   sudo apt update && sudo apt install -y git python3-picamera2 python3-opencv python3-yaml
   ```

2. **On the Pi**: get the code (or `cd ~/desk-talk && git pull` if it's already there):

   ```bash
   git clone https://github.com/randomnoobi/desk-talk.git ~/desk-talk
   ```

3. **On the Pi**: set up the detector container: [raspberry_pi/detector_service/README.md](../detector_service/README.md).
   Without it the camera program still works; your computer then does all the detecting.

Nothing else gets installed for the camera program: it only needs the standard library, the three apt
packages above, and `shared/` from the repo.

## Run it

1. **On your Mac**: start Desk Talk (`uv run python server.py`). It prints your computer's address and the pairing key.
2. **On the Pi**, in the `desk-talk` folder, check everything first:

   ```bash
   cd ~/desk-talk
   python3 -m raspberry_pi.camera --self-test --server http://192.168.1.20:5000 --key AbC123xY
   ```

   It checks the camera, the detector container, the Pi's temperature and that your computer answers,
   one line each, and says what to do about any problem.
3. **On the Pi**, start it (same address and key):

   ```bash
   python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key AbC123xY
   ```

`python3 -m raspberry_pi.camera --help` lists every flag.

## Settings

The camera program asks your computer for its settings (`config/settings.yaml`, the `pi:` section) when it
starts, and again every `pi.settings_check_every_sec`. New `pi.detector` settings are passed to the container
right away; for anything else, restart the camera program. If your computer can't be reached at the start,
it uses the copy of `config/settings.yaml` on the Pi. Flags win over both:

| Flag | What it does |
|---|---|
| `--camera` | `auto`, `picamera2` (camera module), `usb` / `usb:1` (USB webcam), `folder:<dir>` or `video:<file>` for testing. Without it: `pi.camera` in `config/settings.yaml` (`auto`) |
| `--fps` | pictures per second |
| `--photo-dir` | where the pictures are saved on the Pi |
| `--delete-every-min` | delete the saved pictures this often (0 = never) |
| `--keep-last-min` | minutes of pictures a cleanup keeps (0 = delete them all) |
| `--min-free-mb` | if the disk gets this full, delete the oldest pictures right away |
| `--no-detector` | don't use the detector container; your computer detects every picture |
| `--offline` | only take and save pictures; send nothing |
| `--self-test` | check everything, then stop |

## Start it when the Pi boots (optional)

**On the Pi**, in the `desk-talk` folder. Put your own address and key in the first line:

```bash
SERVER=http://192.168.1.20:5000 KEY=AbC123xY
sed -e "s|PI_USER|$USER|g" -e "s|SERVER_URL|$SERVER|" -e "s|PAIR_KEY|$KEY|" raspberry_pi/camera/desk-talk-camera.service \
  | sudo tee /etc/systemd/system/desk-talk-camera.service
sudo systemctl daemon-reload && sudo systemctl enable --now desk-talk-camera
journalctl -u desk-talk-camera -f      # see what it's doing (Ctrl-C to stop watching)
```

## If something's wrong

The log (in the terminal, or `journalctl -u desk-talk-camera -f` **on the Pi**) says what's wrong in plain words.

- **"Cannot reach the computer"**: are both on the same Wi-Fi? Is `server.py` running? On a Mac, allow
  incoming connections for Python if the firewall asks. The Pi keeps taking pictures meanwhile and tries
  again after a short wait that grows each time (up to `pi.upload_retry_max_sec`).
- **"Pairing key does not match"**: copy the key `server.py` printed again.
- **"camera.source: phone"**: set `camera: source: pi` in `config/settings.yaml` on your computer and restart it.
- **"Stopped asking the detector"**: the container isn't answering. **On the Pi**:
  `cd ~/desk-talk/raspberry_pi/detector_service && docker compose ps` and `docker compose logs`.
  The camera program checks again by itself and goes back to using it once it's ready.
- **"The detector has no model yet"**: the first time a model is used, the container converts it, which takes
  a few minutes. Your computer detects the pictures until then.
- **Camera problem**: `rpicam-hello` should show a picture; check the ribbon cable. For a USB webcam use `--camera usb`.
- **"No picture from the camera ... it seems stuck"**: the camera stopped giving pictures for
  `pi.restart_after_missed_pictures` picture times, so the camera program stopped and systemd started it again
  (only when it runs as the service above). If it keeps happening, check the ribbon cable or the USB plug.
