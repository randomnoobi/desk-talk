# Raspberry Pi

The Pi is Desk Talk's camera, and it finds the objects in its own pictures too. Two programs run on it:

- **The camera program** (`raspberry_pi/camera/`) runs on the Pi's own Python 3. It takes **3 pictures
  a second**, keeps them on the Pi and **deletes them every 10 minutes**, asks the detector what's in each
  one, and sends each picture with what was found to your computer over Wi-Fi.
- **The detector** (`raspberry_pi/detector_service/`) runs YOLO inside a Docker container. Only the
  camera program on the Pi itself can reach it.

```
Raspberry Pi                                                       Your computer
┌─────────────────────────────────────────────────────┐            ┌─────────────────────────────┐
│ camera program (python3 -m raspberry_pi.camera)     │ picture +  │ uv run python server.py     │
│   3 pictures a second, saved in ~/desk-talk-photos, │ what was   │ /api/pi/frame               │
│   deleted every 10 minutes                          │ found      │   uses the Pi's detections, │
│       │ picture          ▲ what's in it             │ ─────────▶ │   or runs YOLO itself when  │
│       ▼                  │                          │   Wi-Fi    │   the Pi's detector is off  │
│ detector container (Docker, 127.0.0.1:8100 only)    │            │                             │
└─────────────────────────────────────────────────────┘            └─────────────────────────────┘
```

If the detector is down, still starting or too slow, the camera program sends the pictures anyway and your
computer finds the objects itself. So the camera keeps going whatever happens to the container.

> **Not tried on a real Pi yet.** These steps follow how the camera program and the detector are built
> and tested on the Mac, but nobody has run them on a Raspberry Pi so far, and the detector's Docker image
> hasn't been built yet. If a step doesn't work as written, note what it printed.

More detail: the camera program's [README](camera/README.md) (every flag) and the detector's
[README](detector_service/README.md) (its API, settings and your own model files).

## What you need

- A Raspberry Pi 4 or 5 with the camera module (or a USB webcam). 4 GB of memory or more is best; with
  2 GB it will be tight.
- **64-bit** Raspberry Pi OS (Bookworm). Docker and the detector need it.
- About 3 GB free on the SD card for the detector's image and models.
- The Pi and your computer on the same Wi-Fi.

Check the first two **on the Pi**. This should print `arm64`:

```bash
dpkg --print-architecture
```

And this should print Python 3.11 or newer:

```bash
python3 --version
```

How much space is free:

```bash
df -h ~
```

## Set up the Pi (once)

### 1. Packages for the camera program

**On the Pi**. Raspberry Pi OS already has Python 3, Picamera2 and PyYAML; this makes sure, and adds git
and OpenCV:

```bash
sudo apt update && sudo apt install -y git python3-picamera2 python3-opencv python3-yaml
```

Nothing else gets installed for the camera program: it only needs the standard library, these apt
packages, and the repo's `shared/` folder.

### 2. Get the code

**On the Pi**:

```bash
git clone https://github.com/randomnoobi/desk-talk.git ~/desk-talk
```

### 3. Install Docker

**On the Pi**:

```bash
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
```

Then log out and back in (or `sudo reboot`), so you can use Docker without `sudo`. Check it **on the Pi**:

```bash
docker compose version
```

### 4. Start the detector container

**On the Pi**. The first build downloads PyTorch and Ultralytics, so it takes a while:

```bash
cd ~/desk-talk/raspberry_pi/detector_service && docker compose up -d --build
```

Check that it answers, **on the Pi**. After a minute or so `docker compose ps` should say `healthy`:

```bash
cd ~/desk-talk/raspberry_pi/detector_service && docker compose ps
```

```bash
curl http://127.0.0.1:8100/health
```

It says `"state": "starting"` until the camera program tells it which model to run. Docker starts it again
by itself after a crash and when the Pi boots.

### 5. Run the camera program

1. **On your Mac**, in the `desk-talk` folder, start Desk Talk. It prints the exact command for the Pi,
   with your computer's address and the pairing key (the app shows it too, in the Desk cam panel and in
   Settings):

   ```bash
   uv run python server.py
   ```

2. **On the Pi**, check everything first, with the address and key `server.py` printed:

   ```bash
   cd ~/desk-talk && python3 -m raspberry_pi.camera --self-test --server http://192.168.1.20:5000 --key AbC123xY
   ```

   It checks the camera, the detector container, the Pi's temperature and that your computer answers, one
   line each, and says what to do about any problem. It sends no pictures.

3. **On the Pi**, start it (same address and key):

   ```bash
   cd ~/desk-talk && python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key AbC123xY
   ```

4. **On your Mac**, open http://localhost:5000. The Desk cam panel switches to the live picture.

The first time a model is used, the container converts it to ncnn, which takes a few minutes. Until it's
ready, the pictures go to your computer as `off` and your computer finds the objects. Lens may mention
that the Pi's own detection is off; it goes away once the model is ready.

Useful flags (`python3 -m raspberry_pi.camera --help` lists them all):

| Flag | What it does |
|---|---|
| `--camera` | `auto` (default), `picamera2` (camera module), `usb` / `usb:1` (USB webcam), `folder:<dir>` or `video:<file>` for testing |
| `--no-detector` | don't use the detector container; your computer detects every picture |
| `--offline` | only take and save pictures; send nothing |
| `--self-test` | check everything, then stop |

### 6. Start it when the Pi boots (optional)

**On the Pi**, in the `desk-talk` folder. Put your own address and key in the first line. This fills them
into `raspberry_pi/camera/desk-talk-camera.service` and installs it:

```bash
SERVER=http://192.168.1.20:5000 KEY=AbC123xY
sed -e "s|PI_USER|$USER|g" -e "s|SERVER_URL|$SERVER|" -e "s|PAIR_KEY|$KEY|" raspberry_pi/camera/desk-talk-camera.service \
  | sudo tee /etc/systemd/system/desk-talk-camera.service
sudo systemctl daemon-reload && sudo systemctl enable --now desk-talk-camera
```

The service expects the code in `~/desk-talk`. systemd starts the camera program again when it stops,
including when its watchdog ends it because the camera got stuck.

**On the Pi**, to see what it's doing (Ctrl-C to stop watching):

```bash
journalctl -u desk-talk-camera -f
```

**On the Pi**, to stop it or start it again:

```bash
sudo systemctl stop desk-talk-camera
```

```bash
sudo systemctl restart desk-talk-camera
```

If your computer's address or the pairing key changes, run the commands above again with the new ones.

## Settings

You change the Pi's settings **on your Mac**, in `config/settings.yaml` (the `pi:` section), or just the lines
you change in `config/settings.local.yaml`. Restart `server.py` after a change. The camera program asks your
computer for its settings when it starts and again every minute (`pi.settings_check_every_sec`):

- new `pi.detector` settings (model, format, picture size ...) go to the container right away;
- for anything else, restart the camera program **on the Pi** (`sudo systemctl restart desk-talk-camera`).

If your computer can't be reached when it starts, the camera program uses the copy of `config/settings.yaml`
on the Pi. Flags win over both.

The container's own settings (its port, memory limit and retry waits) are in
`raspberry_pi/detector_service/.env`. After changing one, run `docker compose up -d` again **on the Pi**, in
`~/desk-talk/raspberry_pi/detector_service`. The port must match `pi.detector.url` on your computer.

## If something's wrong

The camera program's log (in the terminal, or `journalctl -u desk-talk-camera -f` **on the Pi**) says what's
wrong in plain words. On your computer, `GET /api/health` shows what the Pi said lately: its detector state
and why, how long detection takes, the model, and how far its clock is off. **On your Mac**:

```bash
curl http://localhost:5000/api/health
```

### The detector container is down

You see "Stopped asking the detector" in the camera program's log, and Lens says the Pi's own detection is
off. Nothing breaks: your computer detects the pictures meanwhile, and the camera program checks the
container now and then by itself and goes back to it once it's ready.

**On the Pi**, see whether it's running, and what it said last:

```bash
cd ~/desk-talk/raspberry_pi/detector_service && docker compose ps
```

```bash
cd ~/desk-talk/raspberry_pi/detector_service && docker compose logs --tail 50
```

**On the Pi**, start it again:

```bash
cd ~/desk-talk/raspberry_pi/detector_service && docker compose up -d
```

- **`/health` says `error`**: the message says why. Often the model name is wrong, or it's your own model
  file and it isn't in the container's `models` volume yet (see the detector's [README](detector_service/README.md)).
  It tries again by itself, waiting longer each time.
- **It keeps restarting** (`docker compose ps` shows "Restarting"): it may be running out of memory. Try a
  smaller model or picture size, or raise `DETECTOR_MEMORY_LIMIT` in `.env` if the Pi has memory to spare.
- **`docker compose up` warns that the memory limit isn't supported**: **on the Pi**, add
  `cgroup_enable=memory cgroup_memory=1` to the end of the line in `/boot/firmware/cmdline.txt`, then
  `sudo reboot`.
- **"The detector has no model yet"**: the container is still converting the model (a few minutes the
  first time). Your computer detects the pictures until then.
- **"speaks version ... of its API"**: the container and the camera program are from different versions of
  the code. Update both (see [Updating](#updating)).

### The Pi is too hot

Lens says the Pi is running hot (from `pi.hot_warning_c`, 75 °C; the Pi slows itself down at about 80 °C),
or that it's slowing itself down. Detection gets slower, so more pictures go out as `busy`.

**On the Pi**, the temperature, and the slow-down flags (`throttled=0x0` means fine):

```bash
vcgencmd measure_temp
```

```bash
vcgencmd get_throttled
```

What helps: a cooler or a small fan, a case with air holes, or less work for the Pi: a smaller model or
picture size (`pi.detector.model`, `pi.detector.image_size`) or fewer pictures a second (`pi.fps`), changed
on your Mac. If Lens says the power supply is too weak, use the official Raspberry Pi power supply.

### Your computer is away

You see "Cannot reach the computer" in the camera program's log. The Pi keeps taking and saving pictures,
and tries again after a short wait that grows each time (up to `pi.upload_retry_max_sec`). When your computer
is back it sends only the newest picture: no catching up on old ones, which would make the characters react
minutes late. The event log on your computer shows the gap.

- Is `server.py` running on your computer? Are both on the same Wi-Fi?
- On a Mac, allow incoming connections for Python if the firewall asks.
- Did your computer's address change? `server.py` prints the new command; if the camera program runs as a
  service, install it again with the new address (step 6).

**On the Pi**, check that your computer can be reached (use its address). Any reply means it can:

```bash
curl http://192.168.1.20:5000/api/health
```

### Other problems

- **"Pairing key does not match"**: copy the key `server.py` printed again (it's kept in `certs/pair-key.txt`
  on your computer).
- **"camera.source: phone"**: set `camera: source: pi` in `config/settings.yaml` on your computer and restart it.
- **"The Pi sends upload format ..."**: the Pi and your computer run different versions of the code. Update
  both (see [Updating](#updating)).
- **Camera problem**: **on the Pi**, `rpicam-hello` should show a picture; check the ribbon cable. For a USB
  webcam use `--camera usb`.
- **"No picture from the camera ... it seems stuck"**: the watchdog stopped the camera program so systemd
  starts it again (only when it runs as the service). If it keeps happening, check the ribbon cable or the
  USB plug.
- **Lens says the Pi's clock is off**: your computer uses its own clock for timing, so nothing breaks, but
  **on the Pi** `timedatectl` should say `System clock synchronized: yes`.

## Updating

Update the Pi and your computer together: each upload carries a version number, and when they don't match
you get a message saying which side to update.

1. **On your Mac**, in the `desk-talk` folder, get the new code and packages, then restart `server.py`:

   ```bash
   git pull && uv sync --all-packages
   ```

   ```bash
   uv run python server.py
   ```

2. **On the Pi**, get the new code:

   ```bash
   cd ~/desk-talk && git pull
   ```

3. **On the Pi**, restart the camera program (or press Ctrl-C and start it again, if it isn't a service):

   ```bash
   sudo systemctl restart desk-talk-camera
   ```

4. **On the Pi**, rebuild and restart the container. Only what changed is rebuilt; it's slow again only when
   `uv.lock` changed:

   ```bash
   cd ~/desk-talk/raspberry_pi/detector_service && docker compose up -d --build
   ```

5. **On the Pi**, check everything (stop the service first, or the camera may be busy):

   ```bash
   cd ~/desk-talk && python3 -m raspberry_pi.camera --self-test --server http://192.168.1.20:5000 --key AbC123xY
   ```

## Choosing the model

The model the Pi runs isn't picked yet. By default it's the same as on your computer,
`yolo26m-objv1-150.pt` (365 kinds of objects, at 640 pixels), which may only manage about one picture a
second on a Pi. The speed test in [`benchmarks/`](../benchmarks/README.md) finds a better fit:

1. Run the benchmark on the Pi, in its own Docker image. The steps, each labelled **on the Pi** or
   **on your Mac**, are in [benchmarks/README.md](../benchmarks/README.md); the full run takes a few hours.
2. **On your Mac**, fetch the results and rebuild the summary (also in that README). The **best pick** in
   [benchmarks/results/README.md](../benchmarks/results/README.md) is the fastest combination that keeps up
   with 3 pictures a second and reaches at least 90% of the best accuracy.
3. Set it **on your Mac**, in `config/settings.yaml` (or `config/settings.local.yaml`), then restart
   `server.py`:

   | Benchmark name, e.g. `yolo11s-480-ncnn-fp16` | Setting |
   |---|---|
   | `yolo11s` | `pi.detector.model: yolo11s.pt` |
   | `480` | `pi.detector.image_size: 480` |
   | `ncnn` | `pi.detector.format: ncnn` |
   | `fp16` / `fp32` | `pi.detector.half: true` / `false` |

   Within a minute the camera program passes the new settings to the container, which converts the model
   (a few minutes the first time; your computer detects meanwhile).

Things to know when you pick:

- The detector container runs only the **`ncnn`** and **`pt`** formats. The benchmark also tries `onnx`, but
  an `onnx` pick can't be used there; take the best `ncnn` (or `pt`) one.
- The `yolo11` models only know the 80 everyday COCO objects: no keys, earphones or pens, which the lost
  items and some characters need. To try models that know those (the Objects365 ones, like the default),
  add them to `config/benchmark.yaml` and run the benchmark again.
- Your computer detects the pictures the Pi couldn't, with its own model (`detector.model`). Using the same
  model on both keeps the objects' names the same.
- A model file of your own (not one Ultralytics can download) has to be copied into the container's
  `models` volume: see the detector's [README](detector_service/README.md).
