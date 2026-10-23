# Detector service (YOLO in Docker on the Pi)

The detector service finds objects in the Pi's pictures, on the Pi itself. It runs Ultralytics YOLO with
the ncnn model format (the fastest choice on the Pi's CPU) inside a Docker container, so PyTorch and the
rest can't clash with the Pi's own packages.

```
camera program (on the Pi) ── POST /detect, picture ──▶ detector service (Docker, 127.0.0.1 only)
                           ◀──────── detections ───────
```

The camera program sends each picture here, then sends the picture and its detections to your computer.
If the service is down, the camera program sends pictures alone and your computer finds the objects, as before.

## What it answers

| | What it does |
|---|---|
| `POST /detect` | Body: one JPEG. Replies with the detections (boxes in the picture's pixels) and how long it took. 400 if it isn't a JPEG, 413 if it's too big, 503 while there's no model yet (the reply says why). |
| `GET /health` | `starting` (no model asked for yet), `loading`, `ready` or `error`, which model is answering, and a message in plain words. |
| `PUT /model` | Body: the model settings (`model`, `format` ncnn or pt, `image_size`, `confidence`, `half`). Loads that model in the background; the old one keeps answering until it's ready. |

The camera program sends `PUT /model` with the `pi.detector` settings from your computer's `config/settings.yaml`.
The service remembers the last one, so after a restart it loads it again by itself.
The request and reply shapes are in `shared/desktalk_shared/detector_api.py`.

The first time a model is used with ncnn, the service converts the `.pt` to ncnn. That takes a few
minutes on the Pi; the result is kept in the `models` volume, so it happens once per model, picture size
and precision. Ultralytics' own models (`yolo11n.pt`, `yolo26m-objv1-150.pt` ...) are downloaded the first
time too. Until a model is ready, `/detect` replies 503 and your computer does the detecting.

YOLO26 models: ncnn can't run their end-to-end head, so Ultralytics runs them with their other head plus the
usual overlap filter. They find slightly different boxes than the `.pt` run the normal way.
`/health` says so (details in `docs/plans/2026-10-21-ncnn-check.md`).

## Set it up (once)

Needs 64-bit Raspberry Pi OS and about 3 GB free on the SD card. A Pi with 4 GB of memory or more is best.

1. **On the Pi**, install Docker, then log out and back in:

   ```bash
   curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
   ```

2. **On the Pi**, get the latest code (clone it first if `~/desk-talk` isn't there yet:
   `git clone https://github.com/randomnoobi/desk-talk.git ~/desk-talk`):

   ```bash
   cd ~/desk-talk && git pull
   ```

3. **On the Pi**, build and start the service. The first build downloads PyTorch and Ultralytics, so it takes a while:

   ```bash
   cd ~/desk-talk/raspberry_pi/detector_service && docker compose up -d --build
   ```

4. **On the Pi**, check it answers. It says `starting` until the camera program tells it which model to run:

   ```bash
   curl http://127.0.0.1:8100/health
   ```

It starts again by itself after a crash and when the Pi boots.

## Try it by hand

**On the Pi**:

```bash
curl -X PUT http://127.0.0.1:8100/model -H 'Content-Type: application/json' \
     -d '{"model": "yolo11n.pt", "format": "ncnn", "image_size": 640, "confidence": 0.35, "half": true}'
curl http://127.0.0.1:8100/health          # "loading" for a few minutes the first time, then "ready"
curl -X POST http://127.0.0.1:8100/detect -H 'Content-Type: image/jpeg' --data-binary @some-photo.jpg
```

## Your own model file

Models that Ultralytics can't download need copying into the `models` volume.

1. **On your Mac**, in the `desk-talk` folder (use your Pi's address):

   ```bash
   scp models/my-model.pt pi@raspberrypi.local:~/
   ```

2. **On the Pi**:

   ```bash
   cd ~/desk-talk/raspberry_pi/detector_service && docker compose cp ~/my-model.pt detector:/models/
   ```

## Settings

The container's settings are in `.env` next to `compose.yaml`. After changing one, run
`docker compose up -d` again **on the Pi**.

| Setting | Default | What it does |
|---|---|---|
| `DETECTOR_PORT` | 8100 | the port, on the Pi's 127.0.0.1 only. Must match `pi.detector.url` in `config/settings.yaml` (the camera program gets it from there). |
| `DETECTOR_MEMORY_LIMIT` | 2g | most memory the container may use; over it Docker restarts it instead of the Pi freezing |
| `DETECTOR_MAX_PICTURE_MB` | 10 | biggest picture `/detect` accepts |
| `DETECTOR_RETRY_FIRST_SEC` | 5 | wait before trying a model that failed to load again; doubles each time ... |
| `DETECTOR_RETRY_MAX_SEC` | 300 | ... up to this |

Which model it runs comes from the camera program (`PUT /model`), not from here.

## Day to day

**On the Pi**, in `~/desk-talk/raspberry_pi/detector_service`:

| | |
|---|---|
| `docker compose logs -f` | see what it's doing |
| `docker compose ps` | is it running and healthy? |
| `git pull && docker compose up -d --build` | update it |
| `docker compose down` | stop it (the models stay in the volume) |
| `docker compose down --volumes` | stop it and delete the models volume (the next start downloads and converts again) |

## On your Mac (for working on it)

**On your Mac**, in the `desk-talk` folder:

```bash
uv run pytest tests/unit -k detector_service                        # fast tests, with a fake YOLO
uv run pytest -m slow tests/slow/test_detector_service_real_export.py   # real yolo11n.pt (in models/), converted to ncnn
uv run pytest -m docker tests/docker                                # builds and runs the image (needs Docker)
```

Run the service without Docker (models in `models/`):

```bash
DETECTOR_HOST=127.0.0.1 DETECTOR_MODELS_DIR=models \
    uv run --env-file raspberry_pi/detector_service/.env python -m desktalk_detector
```

Apple Silicon Macs are 64-bit ARM like the Pi, so `docker compose up -d --build` builds the same image there.

## If something's wrong

- **`/health` says `error`**: the message says why. Often the model name is wrong, or it's your own model and
  isn't in the volume yet (see "Your own model file"). It tries again by itself, waiting longer each time.
- **`docker compose up` warns that the memory limit isn't supported**: the Pi's memory controls are off.
  **On the Pi**, add `cgroup_enable=memory cgroup_memory=1` to the end of the line in
  `/boot/firmware/cmdline.txt`, then `sudo reboot`.
- **It keeps restarting** (`docker compose ps` shows "Restarting"): it may be running out of memory. Try a
  smaller model or picture size, or raise `DETECTOR_MEMORY_LIMIT` in `.env` if the Pi has memory to spare.
- **Slow**: see `/health` (`last_detect_ms`). A smaller model or picture size helps, and so does a cooler: the
  Pi slows itself down when it gets hot.
