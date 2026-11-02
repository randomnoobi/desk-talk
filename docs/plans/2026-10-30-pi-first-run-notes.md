# First run on the real Pi

Task 2 of the [Pi standalone plan](2026-10-30-pi-standalone-refactor-plan.md), run on 2026-11-01. No product code changed.

**Short version:** the camera works inside a container, so the plan can go on. Three things need fixing or a decision first: the first pick (`yoloe-26s-seg`) fails on every picture until a one-line bug is fixed; the current setting manages only 1.7 pictures a second, not the 3 the app takes; and Docker's memory limit does nothing on this Pi until a boot setting changes. Nobody has heard the voice yet.

## The Pi

| | |
|---|---|
| Model | Raspberry Pi 5 Model B Rev 1.1 |
| Memory | 8 GB (7.9 GiB seen), 2 GB swap |
| Disk | 119 GB SD card (`mmcblk0`, SM128). 90 GB free after all this |
| System | Debian 13 trixie, kernel 6.18.50, Docker 29.9.2, time zone America/Los_Angeles |
| Camera | imx708 |
| Cooling | a fan is there (`pwmfan` shows up in the system) |
| Sound | only the two HDMI outputs, both with nothing plugged in. No USB speaker yet |

## The camera image

- **Base:** `debian:bookworm-slim` with Raspberry Pi's bookworm apt source. It worked the first time, so `debian:trixie-slim` was not tried.
- **Versions inside:** Python 3.11.2, libcamera 0.5.2. The Pi itself has libcamera 0.7.2. The older one inside the container talks to the newer system without trouble.
- **Size:** 1.43 GB on disk (351 MB downloaded). `python3-opencv` is most of it.
- **File:** `raspberry_pi/camera/Dockerfile`, with `Dockerfile.dockerignore` next to it. The build sees only `shared/desktalk_shared`, `raspberry_pi/camera` and `config/settings.yaml`.

### What it needs to reach the camera

I took each one away to see whether it mattered.

| Needed | Without it |
|---|---|
| every `/dev/video*` (25 here) and `/dev/media*` (4) | no camera |
| every `/dev/v4l-subdev*` (4). The plan didn't list these | `list index out of range` |
| `/dev/dma_heap` (a whole folder is fine) | `Could not open any dmaHeap device`. The plan didn't list this either |
| `/run/udev` read-only | `list index out of range` |
| the `video` group (44), when not running as root | `Permission denied` on `/dev/media*` |
| `/dev/vchiq` | **not needed: a Pi 5 doesn't have it** |

It runs fine without root: `--user 1000:1000 --group-add 44 -e HOME=/tmp`.

`Failed to open /dev/dma_heap/vidbuf_cached` shows in the log every time, and is harmless. That name is a link Docker doesn't copy in, and libcamera moves on to the next one.

## Commands that worked

**On your Mac**, in the repo folder, copy the code (tracked files only, so no secrets go over; a new file needs `git add` or its own copy first):

```bash
git ls-files -z | rsync -a --from0 --files-from=- ./ pi:desk-talk-refactor/
```

**On the Pi**, build the camera image and collect the device flags:

```bash
cd ~/desk-talk-refactor
docker build -f raspberry_pi/camera/Dockerfile -t desktalk-camera:local .

CAMFLAGS=""
for d in /dev/video* /dev/media* /dev/v4l-subdev* /dev/dma_heap/*; do
  [ -e "$d" ] && [ ! -L "$d" ] && CAMFLAGS="$CAMFLAGS --device $d"
done
CAMFLAGS="$CAMFLAGS -v /run/udev:/run/udev:ro"
```

**On the Pi**, run the self-test. `--network host` is what lets it reach the detector at `127.0.0.1` for now:

```bash
docker run --rm --network host $CAMFLAGS --user 1000:1000 --group-add 44 -e HOME=/tmp desktalk-camera:local \
  python3 -m raspberry_pi.camera --self-test --server http://127.0.0.1:9 --key placeholder
```

**On the Pi**, start the detector:

```bash
cd ~/desk-talk-refactor/raspberry_pi/detector_service && docker compose up -d --build
```

**On the Pi**, copy a model in and try it:

```bash
docker compose cp ~/pi-first-run/models/yolo26m.pt detector:/models/
curl -X PUT http://127.0.0.1:8100/model -H 'Content-Type: application/json' \
     -d '{"model": "yolo26m.pt", "format": "ncnn", "image_size": 480, "confidence": 0.35, "half": true}'
curl http://127.0.0.1:8100/health
curl -X POST http://127.0.0.1:8100/detect -H 'Content-Type: image/jpeg' --data-binary @picture.jpg
```

## Results

### Camera

Self-test output, with the camera on `auto`:

```
OK       camera       picamera2: took a picture (29 KB, light 420.6 lux)
OK       detector     ready: yolo26m-objv1-150.pt (ncnn, 640 pixels)
OK       temperature  49 °C
PROBLEM  computer     Cannot reach the computer at http://127.0.0.1:9 (it's not running (connection refused)). Is server.py running, and on the same Wi-Fi?
1 of 4 checks found a problem.
```

The computer line was expected, since no app is running. It does make the self-test exit with 1.

- `--self-test` needed no code changes.
- `--offline` ran for 20 seconds and saved 59 pictures (3 a second) at about 6 to 8% of one processor core.
- After `docker kill` in the middle of that run, the next container opened the camera straight away. A crash doesn't leave it stuck.

### Detector

`docker compose up -d --build` built and started it, and `/health` answered. It replaced the old container and kept the `models` volume. The memory warning is covered under "What later tasks need to know".

**The camera is pointing at the ceiling** (a lamp and some panelling), so the real picture has nothing in it to find, and all the models correctly found nothing. To show they do find things, I also sent `bus.jpg`, the stock photo that comes with Ultralytics. **No model has yet found an object in a picture from this camera.**

| Setting | Camera picture | `bus.jpg` |
|---|---|---|
| `yoloe-26s-seg-desk` 640 fp16 (with the throwaway fix below) | nothing | 4 person |
| `yolo26m-objv1-150` 480 fp32 | nothing | 11: person, bus, sneakers, glasses |
| `yolo26m` 480 fp16 | nothing | 5: bus, 4 person |
| `yolo26m-objv1-150` 640 fp32 | nothing | 11: person, bus, sneakers, glasses |

`yoloe` not finding the bus is right: "bus" isn't one of the words in its desk list.

### Speed, memory and temperature

Each model ran for 90 seconds, sending the camera's 1280 x 720 picture one after another as fast as answers came back, with no errors. The Pi was doing nothing else, apart from my memory and temperature checks every 10 seconds, which took a little processor time.

| Setting | Pictures/s | Earlier bench | Typical ms | 95% within ms | Memory, fresh start | Peak in the run | Hottest | Throttled |
|---|---|---|---|---|---|---|---|---|
| `yoloe-26s-seg-desk` 640 fp16 (throwaway fix) | **3.72** | 3.8 | 266 | 294 | 596 MB | 799 MB | 58.7 °C | `0x0` |
| `yolo26m-objv1-150` 480 fp32 | **3.05** | 3.2 | 325 | 363 | 686 MB | 1229 MB | 56.5 °C | `0x0` |
| `yolo26m` 480 fp16 | **3.22** | 3.0 | 308 | 344 | 723 MB | 1284 MB | 60.4 °C | `0x0` |
| `yolo26m-objv1-150` 640 fp32 (**current setting**) | **1.68** | - | 590 | 628 | 841 MB | 1575 MB | 59.8 °C | `0x0` |

- **Only `yoloe` keeps 3 pictures a second with room to spare.** The other two picks get there, but their 95% figures miss the 333 ms goal here, and my checks may account for part of that.
- **The current setting can't keep up with 3 pictures a second.**
- **Memory:** "fresh start" is after a restart with the model already converted (peak after 12 pictures), and was measured separately from the runs. "Peak in the run" is higher for the last three because converting a model in the same process (or running after a conversion) leaves extra memory in use until a restart. Their runs came straight after other models and conversions, and I didn't test how far each cause adds to it. Treat 1.6 GB as the most seen, and 600 to 850 MB as normal once running.
- **How memory was measured:** `docker stats` shows `0B / 0B` on this Pi (see below), so these are the service's own `VmRSS` and `VmHWM` from `/proc/1/status`. `docker stats` still gave processor use: 77 to 139%.
- **Converting** to ncnn took 24 to 36 seconds on the Pi, not the "few minutes" the detector's README says. Loading a model already converted took 3 to 4 seconds.

### Speech

**Nobody has heard this yet.** There is no speaker plugged in, and both HDMI outputs refuse to open with nothing attached (`aplay: audio open error: Unknown error 524`). So these numbers are the time until the first sound data comes out of the program, read through a pipe. **A person must check it's clear once the speaker is in**, and the time to actually start playing on a speaker comes on top of these and hasn't been measured.

The test used the Piper 2023.11.14-2 program in a throwaway container with `/dev/snd`, and 4 sentences of 3 to 4 seconds each, 6 tries per voice.

"Busy" means the camera running and `/detect` in a loop on the current setting (the slowest one).

| | Idle: typical / worst | Busy: typical / worst |
|---|---|---|
| Piper `en_US-lessac-low`, new program per sentence | 0.61 / 1.31 s | **1.27 / 1.37 s** |
| Piper `en_US-lessac-medium`, new program per sentence | 0.72 / 1.37 s | **1.43 / 1.52 s** |
| Piper low, kept running | 0.25 / 0.27 s | up to 0.72 s |
| Piper medium, kept running | 0.33 / 0.37 s | up to 0.81 s |
| `espeak-ng` (first bytes are the file header, so this flatters it) | 0.008 / 0.182 s | 0.015 / 0.02 s |

- **Under 2 seconds in every case**, even with a new program loading the voice each time. Keeping one Piper program running cuts the wait to well under a second, and a bigger voice costs little.
- **Busy, kept-running figures are rough.** Only the first three sentences are clean: in the last three my test read the end of one sentence as part of the next, so I give the worst figure only.
- **Piper sends a sentence's sound once it's made**, so a long sentence takes longer to start. Split long text into sentences.
- **The detector slowed while Piper worked:** 1.15 pictures/s (0.87 s each, by `curl`) against 1.68. That test had Piper speaking almost nonstop, so normal use will cost much less. The Pi stayed at 59.3 °C with throttled `0x0`.
- Without root, `/dev/snd` needs the `audio` group (29): `--user 1000:1000 --group-add 29`. Without it, `aplay` says no sound cards are found.
- The voices came from `huggingface.co/rhasspy/piper-voices` and the program from `github.com/rhasspy/piper`, so the real image will have to download them when it's built.

## What later tasks need to know

### Must fix

1. **`yoloe-26s-seg` fails on every picture.** `ModelFolder.open` in `raspberry_pi/detector_service/desktalk_detector/model_folder.py` always loads the ncnn model with `task='detect'`, but this model's task is `segment`.
   - The numbers come out in the wrong places, so `/detect` answers 500 with `KeyError: 50`, even for the empty ceiling picture.
   - `/health` still says `ready` the whole time, which hides it.
   - The benchmarks passed the right task, so they never saw it.
   - For the numbers above I mounted a one-line change over that file in a throwaway compose override. It is not in the repo. The real fix should read `task` from the `metadata.yaml` the conversion writes next to the model.
2. **The camera's detector address.** `pi.detector.url` is `http://127.0.0.1:8100`. Inside a container that is the container itself, so the first self-test said connection refused with the detector running. In the three-container setup it needs to be `http://detector:8100`.
3. **Pick the model on purpose.** `pi.detector` in `config/settings.yaml` still says `yolo26m-objv1-150.pt` at 640, the 1.68 pictures/s setting.

### Compose settings for `camera`

4. **Listing devices.** Compose has no wildcard for `/dev/video*`, and there are 33 names to list. Numbers might change between boots or kernels. Binding all of `/dev` with cgroup rules also worked:
   `-v /dev:/dev -v /run/udev:/run/udev:ro --device-cgroup-rule='c 81:* rmw' --device-cgroup-rule='c 510:* rmw' --device-cgroup-rule='c 252:* rmw'`.
   But 510 and 252 are numbers the kernel hands out, so they might change as well. Pick one for Task 3, and check it after a reboot, which I did not try.
5. **Saved pictures vanish with the container.** `pi.photo_dir` is `~/desk-talk-photos`, inside the container. It needs a volume, and `HOME` needs setting when not running as root.
6. **Time zone.** Containers run in UTC, and the Pi is in Los Angeles. Anything using `clock.local_hour` or `clock.local_day` in a container would be 8 hours off (in November) without `TZ` or `/etc/localtime` set. That matters most for `app`.

### The Pi's health checks

7. **Throttling can't be read.** `HealthReader` looks for a `get_throttled` file in `/sys`, and on this kernel there is none anywhere (`find /sys -name get_throttled` finds nothing), so the self-test can't report it. `vcgencmd` works but needs `/dev/vcio`, which is root only. `/sys/class/hwmon` has `rpi_volt`, which may cover the power warning. I haven't checked that.
8. **Temperature does work.** `/sys/class/thermal` can be read inside the container.

### Docker on this Pi

9. **Fixed on 2026-11-01:** the user turned memory limits on and rebooted, and a test container over its limit now gets stopped (exit 137). What follows is how it was. **`mem_limit` did nothing.** The Pi started with `cgroup_disable=memory` (`/proc/cmdline`). Compose says so when it starts the detector: `Your kernel does not support memory limit capabilities ... Limitation discarded`. So nothing stops a container using all the memory, and `docker stats` shows no memory. The fix needs sudo and a reboot, so I didn't run it and it is **untested**. **On the Pi**:

   ```bash
   sudo sed -i '$ s/$/ cgroup_enable=memory cgroup_memory=1/' /boot/firmware/cmdline.txt && sudo reboot
   ```

10. **Wrong hint on some camera errors.** With a device or `/run/udev` missing, the self-test says `list index out of range`, then blames the ribbon cable. In a container, the likely cause is the compose settings.
11. **`--self-test` exits with 1 when there's no computer.** A Pi that stands alone shouldn't count that as a failure.
12. **A harmless warning from the detector.** `user config directory '/home/detector/.config/Ultralytics' is not writable`. Setting `YOLO_CONFIG_DIR` would stop it.
13. **SD card space.** The build cache is 10 GB, 8 GB of it reclaimable with `docker builder prune` **on the Pi**. I haven't run that.

## How the Pi was left

- **Detector:** the new `desktalk-detector` container from `~/desk-talk-refactor` is running, without the throwaway fix, on `yolo26m-objv1-150.pt` at 640. The volume now holds the three `.pt` files and their ncnn versions.
- **Old detector:** `~/desk-talk` wasn't touched. To go back, **on the Pi**: `cd ~/desk-talk/raspberry_pi/detector_service && docker compose up -d --build`.
- **Camera:** image `desktalk-camera:local` is there, built from the committed Dockerfile, with nothing running from it. The tests above ran on the same build before a comment was added to the Dockerfile; the self-test passed again on the final image.
- **Test files:** the scripts, logs, model copies, Piper test files and throwaway fix are in `~/pi-first-run`. The `piper-test` image has been removed.
