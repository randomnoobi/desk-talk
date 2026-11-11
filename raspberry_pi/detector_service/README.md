# Detector service (the `detector` container on the Pi)

The detector service finds objects in the Pi's pictures, on the Pi itself. It runs Ultralytics YOLO with
the ncnn model format (the fastest choice on the Pi's CPU) inside a Docker container, one of the Pi's three
(`raspberry_pi/compose.yaml`).

```
camera container ── POST /detect, picture ──▶ detector container (http://detector:8100, no open port)
                 ◀──────── detections ───────
```

The camera sends each picture here, then sends the picture and its detections to the app. If the service is down,
the camera sends pictures alone, with nothing found, until it is back: there is no backup detector. It has no port
open on the Pi: only the camera and app containers can reach it, by its name, `detector`.

## What it answers

| | What it does |
|---|---|
| `POST /detect` | Body: one JPEG. Replies with the detections (boxes in the picture's pixels) and how long it took. 400 if it isn't a JPEG, 413 if it's too big, 503 while there's no model yet (the reply says why). |
| `GET /health` | `starting` (no model asked for yet), `loading`, `ready` or `error`, which model is answering, and a message in plain words. |
| `PUT /model` | Body: the model settings (`model`, `format` ncnn or pt, `image_size`, `confidence`, `half`). Loads that model in the background; the old one keeps answering until it's ready. |

The camera sends `PUT /model` with the `pi.detector` settings from the app (`config/settings.yaml`, with
`raspberry_pi/settings.pi.yaml` and then your own `raspberry_pi/mine/settings.mine.yaml` laid over it).
The service remembers the last one, so after a restart it loads it again by itself.
The request and reply shapes are in `shared/desktalk_shared/detector_api.py`.

The first time a model is used with ncnn, the service converts the `.pt` to ncnn. That takes a few
minutes on the Pi; the result is kept in the `models` volume, so it happens once per model, picture size
and precision. Until a model is ready, `/detect` replies 503 and no objects are found.

YOLO26 models: ncnn can't run their end-to-end head, so Ultralytics runs them with their other head plus the
usual overlap filter. They find slightly different boxes than the `.pt` run the normal way.
`/health` says so (details in `docs/plans/2026-10-21-ncnn-check.md`).

## Set it up

You don't, by hand: `bash ~/desk-talk/raspberry_pi/install.sh` builds the image, fetches the model named in
`pi.detector.model` once and copies it into the `models` volume, and starts the container with the other two
([raspberry_pi/README.md](../README.md)). It needs 64-bit Raspberry Pi OS; the image is big, so use a 32 GB SD card or larger.
It starts again by itself after a crash and when the Pi boots. The first build downloads PyTorch and Ultralytics,
so it takes a while.

## Asking it by hand

The port isn't open on the Pi, so `curl http://127.0.0.1:8100` doesn't answer. Ask from inside the container.
**On the Pi**, in `~/desk-talk/raspberry_pi`:

```bash
docker compose exec detector python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8100/health').read().decode())"
```

It says `starting` until the camera tells it which model to run, then `loading` for a few minutes the first time, then
`ready`. The app also shows what the camera last heard from it: `curl http://localhost:5000/api/health` on the Pi, its `pi` part.
`POST /detect` and `PUT /model` are what the camera calls, so there is no need to call them by hand; the tests below
call them with a picture.

## Your own model file

It never downloads a model on the Pi (`YOLO_OFFLINE=true` in the Dockerfile, which also stops Ultralytics sending usage data), so every model needs copying into the `models` volume.

1. **On your Mac**, in the `desk-talk` folder (use your Pi's address, usually `desktalk.local`):

   ```bash
   scp models/my-model.pt <your user>@desktalk.local:~/
   ```

2. **On the Pi**:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose cp ~/my-model.pt detector:/models/
   ```

3. Name it in `pi.detector.model` in `raspberry_pi/mine/settings.mine.yaml`, then `docker compose restart app camera`.

## Settings

The container's settings are in `raspberry_pi/.env`, next to `raspberry_pi/compose.yaml`. After changing one, run
`docker compose up -d` again **on the Pi**.

| Setting | Default | What it does |
|---|---|---|
| `DETECTOR_PORT` | 8100 | the port inside Docker's own network, reachable only by the other two containers (it isn't published on the Pi). Must match `pi.detector.url` in `raspberry_pi/settings.pi.yaml`. |
| `DETECTOR_MEMORY_LIMIT` | 2g | most memory the container may use; over it Docker restarts it instead of the Pi freezing |
| `DETECTOR_MAX_PICTURE_MB` | 10 | biggest picture `/detect` accepts (1 MB = 1024 × 1024 bytes) |
| `DETECTOR_RETRY_FIRST_SEC` | 5 | wait before trying a model that failed to load again; doubles each time ... |
| `DETECTOR_RETRY_MAX_SEC` | 300 | ... up to this |
| `DETECTOR_HEALTH_INTERVAL` | 30s | how often Docker asks `/health` ... |
| `DETECTOR_HEALTH_TIMEOUT` | 5s | ... how long one answer may take ... |
| `DETECTOR_HEALTH_START_PERIOD` | 60s | ... how long after a start failed answers don't count yet (loading PyTorch is slow) ... |
| `DETECTOR_HEALTH_RETRIES` | 3 | ... and how many failed answers in a row make it `unhealthy` |
| `DETECTOR_HEALTH_FAILS_BEFORE_RESTART` | 10 | Docker leaves an unhealthy container hung, so after this many failed answers in a row the check starts it again (`raspberry_pi/health_check.py`) ... |
| `DETECTOR_HEALTH_GRACE_SEC` | 300 | ... not counting this long after a start ... |
| `DETECTOR_HEALTH_ANSWER_WAIT_SEC` | 4 | ... and giving one answer this long, less than `DETECTOR_HEALTH_TIMEOUT` |
| `JOURNAL_MAX_USE` | 200M | its logs go to the Pi's journal, with the other two's, which takes at most this much of the SD card |

Which model it runs comes from the camera (`PUT /model`), not from here.

## Day to day

**On the Pi**, in `~/desk-talk/raspberry_pi` (the compose file is there, and starts all three):

| | |
|---|---|
| `docker compose logs -f detector` | see what it's doing |
| `docker compose ps` | is it running and healthy? |
| `bash update.sh` | update it, with the other two |
| `docker compose stop detector` | stop just this one (no objects are found meanwhile) |
| `docker compose down` | stop all three (the models and the pairing key stay in their volumes) |
| `docker compose down --volumes` | stop all three and delete the volumes: the models (copy them in again, or run `install.sh`) **and the pairing key** |

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
    uv run --env-file raspberry_pi/.env python -m desktalk_detector
```

Apple Silicon Macs are 64-bit ARM like the Pi, so `docker compose build detector` (in `raspberry_pi/`) builds the same image there.

## If something's wrong

- **`/health` says `error`**: the message says why. Often the model name is wrong, or it's your own model and
  isn't in the volume yet (see "Your own model file"). It tries again by itself, waiting longer each time.
  It also says `error` (with the model still named) when the model loaded but the last picture failed. It goes back
  to `ready` by itself when a picture works.
- **`docker compose up` warns that the memory limit isn't supported**: the Pi's memory controls are off.
  **On the Pi**, add `cgroup_enable=memory cgroup_memory=1` to the end of the line in
  `/boot/firmware/cmdline.txt`, then `sudo reboot`.
- **It keeps restarting** (`docker compose ps` shows "Restarting"): it may be running out of memory. Try a
  smaller model or picture size, or raise `DETECTOR_MEMORY_LIMIT` in `.env` if the Pi has memory to spare.
- **Slow**: see `/health` (`last_detect_ms`). A smaller model or picture size helps, and so does a cooler: the
  Pi slows itself down when it gets hot.
