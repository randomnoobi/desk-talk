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

# Task 10: choosing the Pi's settings (2026-11-02)

Measured on the same Pi 5 (8 GB, fan, SD card), with all three containers from a real install running, memory limits
on, and nobody at the desk. The camera still points at the ceiling, so most rows have nothing in the picture.

**Short version:** `yolo26s-objv1-150.pt` at 640 (fp32), 3 pictures a second, 1280 x 720, quality 80, pictures kept in
memory. It looks at every picture (3.0 a second, 200 to 215 ms each) using about 2 of the 4 cores, the hottest seen was
60.4 °C, and a bottle "put down" shows up as an `object_state` in 3.9 to 4.3 s. The pictures no longer go to the SD card,
which cut its writes from about 470 to 580 MB an hour to about 35. It is in `raspberry_pi/settings.pi.yaml`.

## How it was measured

- Each row: write the settings into `raspberry_pi/mine/settings.mine.yaml`, restart `app` and `camera`, wait until the
  app says the detector runs that model, restart `detector` (so memory left over from converting doesn't count), wait
  40 s, then measure for 240 s. One thing changes at a time.
- **ok/s**: pictures the detector answered, per second, from the camera's own counts. **busy**: pictures that went to the
  app for the live view only, because the detector was still on the last one.
- **detect ms**: what the camera says each `/detect` took. **to app**: from the picture being taken to the app logging
  it (`ts - taken_at` in `detections.jsonl`).
- **Memory**: each container's own `memory.current` and `memory.peak` (cgroup). **SD MB/h**: everything written to
  `mmcblk0`, and in brackets what the camera container wrote.
- **Page / live ms**: the home page, `/api/status`, `/api/latest_frame_raw/0`, and the first 2 KB of the live view
  (`/api/video_feed_raw`), asked every 5 s. Typical, and the slowest of all four.
- Temperature and throttle flags from `vcgencmd` on the Pi itself, every 5 s.
- The scripts and the raw results are on the Pi in `~/task10/` (`trial.py`, `results.jsonl`).

## The table

The swap columns were all 0 pages in and out, with 0 MB of swap used, so they're left out. No container restarted.
Throttle flags were `0x0` in every row.

| Settings (one change from the row it's compared with) | ok/s | busy | detect ms typical / 95% | to app s typical / 95% | hottest °C | processor busy | detector cores | memory MB, peak: detector / app / camera | SD MB/h (camera) | page / live ms typical (slowest) |
|---|---|---|---|---|---|---|---|---|---|---|
| `yolo26m-objv1-150` 640 fp32, the old setting | 1.49 | 322 | 599 / 623 | 0.78 / 0.93 | 67.5 | 79% | 2.9 | 607 / 105 / 102 | 471 (355) | 4.7 / 3.0 (35) |
| same, 480 | 2.36 | 140 | 323 / 340 | 0.46 / 0.54 | 66.4 | 66% | 2.5 | 535 / 106 / 113 | 496 (368) | 6.2 / 5.0 (12) |
| same, 480 fp16 | 2.07 | 199 | 328 / 348 | 0.52 / 0.66 | 60.9 | 61% | 2.2 | 535 / 103 / 115 | 573 (399) | 4.6 / 3.8 (21) |
| same, 544 fp16 | 1.51 | 318 | 432 / 449 | 0.61 / 0.77 | 60.9 | 57% | 2.1 | 556 / 106 / 125 | 561 (390) | 5.4 / 5.0 (18) |
| same, 480, 4 pictures a second | 2.01 | 423 | 328 / 356 | 0.47 / 0.59 | 58.7 | 58% | 2.1 | 535 / 101 / 131 | 746 (556) | 1.9 / 1.6 (6) |
| same, 480, 5 pictures a second | 2.50 | 531 | 333 / 343 | 0.41 / 0.53 | 62.0 | 72% | 2.6 | 535 / 109 / 147 | 929 (708) | 2.9 / 2.6 (17) |
| `yolo26m` (80 COCO kinds) 480 fp16 | 2.87 | 32 | 311 / 332 | 0.48 / 0.63 | 64.2 | 79% | 2.9 | 481 / 111 / 116 | 569 (399) | 3.8 / 3.0 (12) |
| `yoloe-26s-seg-desk` 640 fp16 | 2.95 | 14 | 273 / 302 | 0.49 / 0.61 | 62.0 | 69% | 2.5 | 503 / 105 / 114 | 564 (399) | 8.2 / 2.8 (19) |
| `yolo26s-objv1-150` 640 fp16 | 3.02 | 0 | 213 / 226 | 0.37 / 0.54 | 60.4 | 53% | 1.9 | 516 / 102 / 115 | 568 (399) | 4.5 / 3.3 (16) |
| **`yolo26s-objv1-150` 640 fp32 (chosen)** | **3.00** | 2 | 214 / 222 | 0.40 / 0.55 | 59.8 | 53% | 1.9 | 516 / 106 / 121 | 578 (399) | 3.9 / 3.3 (20) |
| chosen, 4 pictures a second (fp16) | 3.94 | 17 | 214 / 225 | 0.39 / 0.47 | 61.5 | 68% | 2.5 | 517 / 102 / 133 | 714 (513) | 3.8 / 3.6 (15) |
| chosen, 960 x 540 pictures | 3.01 | 0 | 211 / 221 | 0.37 / 0.53 | 59.8 | 52% | 1.9 | 511 / 96 / 105 | 449 (266) | 2.0 / 1.6 (14) |
| chosen, 640 x 360 pictures | 3.03 | 0 | 209 / 221 | 0.36 / 0.53 | 60.4 | 51% | 1.9 | 509 / 91 / 91 | 317 (133) | 5.7 / 2.0 (11) |
| chosen, `jpeg_quality` 60 | 3.01 | 0 | 214 / 223 | 0.42 / 0.54 | 60.4 | 53% | 1.9 | 516 / 104 / 106 | 503 (311) | 4.7 / 3.5 (12) |
| chosen, pictures in memory (tmpfs), real camera, 12 min | 3.02 | 0 | 212 / 223 | 0.40 / 0.55 | 60.4 | 52% | 1.9 | 1174 / 127 / 160 | **34 (0)** | 4.3 / 3.3 (10) |
| chosen, pictures in memory, a full desk picture (see below), 4 min | 3.03 | 0 | 200 / 208 | 0.37 / 0.52 | 58.7 | 50% | 1.9 | 518 / 117 / 163 | 73 (0) | 1.8 / 1.6 (24) |
| chosen, pictures in memory, the same full desk picture, **1 hour** | 3.02 | 12 | 198 / 209 | 0.39 / 0.53 | **58.7** | 50% | 1.9 | **1174** / 122 / 209 | 86 (0) | 6.1 / 4.3 (19) |

What it shows:

- **The detector only gets a picture when the camera takes one.** A detector that needs a little more than a third of
  a second misses every other picture: `yolo26m-objv1-150` at 480 took 323 ms and managed 2.4 a second, not 3. More
  pictures a second doesn't help much (4 a second gave 2.0 a second, 5 gave 2.5) and costs processor and disk.
- **fp16 was no faster than fp32** for these models on this Pi, and slower for the medium one at 480 (2.07 against 2.36).
- **Picture size and JPEG quality barely change the speed** (51 to 53% of the processor either way). They only change
  how much goes to the disk, and with the pictures in memory that's none. So 1280 x 720 at 80 stays: the live view
  and the trace pictures stay sharp, and the sip settings were tuned on 1280 x 720 pictures.
- **Temperatures** of the first rows (up to 67.5 °C) were in the afternoon, right after a reboot that converted models;
  the later rows were in the evening. The room's temperature wasn't measured.

## Why this model

- **It knows the app's words.** The app's characters and habit rules (`config/agents.yaml`, `config/protocols.yaml`)
  are written for the Objects365 words of `yolo26m-objv1-150`. `yolo26s-objv1-150` has the same 365 words, so the
  same 57 of the 70 words in `agents.yaml` (the other 13, like "mug" and "earbuds", are spare names neither knows).
  `yolo26m` knows only the 80 COCO ones: 47 of the 70 mean nothing to it (no keys, pens, earphones, glasses, watch,
  lamp, cable ...). `yoloe-26s-seg-desk` has 46 words of its own, and 30 of the 70 aren't among them (no keys,
  glasses, scissors, watch, plant, lamp ...).
- **install.sh can fetch it.** `yolo26s-objv1-150.pt` is at `INSTALL_MODEL_URL` (v8.4.0), and its checksum is now in
  `raspberry_pi/model_checksums.txt` (hashed from that download; the same as the copy on the Mac). `yoloe-26s-seg-desk.pt`
  is made on the Mac by the benchmark and isn't there (404), so install.sh couldn't fetch it.
- **Accuracy on the desk test** (from `benchmarks/results/desk/`, on the laptop): desk score 0.44 for this one at 640,
  against 0.52 for `yolo26m-objv1-150` at 640 and 0.46 at 480. On Sam's own 6 desk photos all three objv1 settings
  score 0.64 to 0.74. So it is a little less sure on the public photos, but it looks at twice as many pictures as the
  old setting, with half the processor.

## The app's waits at this speed (step 4, as far as it goes with nobody at the desk)

Nobody was there to put a cup down, so I used one of Sam's own Pi camera photos of the desk
(`data/bench/desk-photos/desk_03.jpg`: laptop, mouse, phone, a water bottle, a hand) and made two more from it: the
bottle painted out, and the bottle moved 45 px to the right. The camera container played them with `pi.camera:
folder:/scene`, and swapping the one file in that folder was "putting the bottle down" at a known moment. The app ran on
a throwaway data folder for these tries, so the real one didn't get their events.

| Settings | Bottle shown until `object_state` "appeared" (5 tries) | Bottle gone until "removed" (5 tries) | First picture with the bottle reaching the app |
|---|---|---|---|
| **chosen** | **3.93 to 4.31 s** (typical 4.27) | 3.95 to 4.34 s | 0.26 to 0.64 s |
| `yolo26m-objv1-150` 640, the old setting | 5.30 to 5.96 s | 5.91 to 6.00 s | |
| `yolo26m-objv1-150` 480 | 4.27 to 4.66 s | 4.28 to 4.64 s | |

- That's `object_states.appear_sec` (3 s) plus the vote of the last 5 pictures plus the trip to the app. The old
  setting misses the 5 s target; the chosen one meets it, with these pictures. **No change to `object_states` was
  needed.**
- **Sips:** gone for 4 s, then back 45 px away (a "person" was in every picture, the hand): **10 of 10 counted**. Gone
  for 4 s and back in exactly the same place: 0 of 5 counted, as it should be. This is the rule working at this speed
  with swapped pictures, **not** a real sip: a real hand lifting a real cup is still to be tried.
- **Chat** with no AI set answers in 3 to 14 ms while detecting (it says chat isn't set up). The "where are my keys?"
  question answers from the app itself, also in a few ms. Chat with an AI wasn't tried: none is set up.

## The SD card (step 5)

The SD card is the Pi's only disk (`/dev/mmcblk0p2`, ext4). Before, the camera wrote **355 to 400 MB an hour** of
pictures to it (more at more pictures a second), each deleted 10 minutes later. Now the pictures are in memory:
`compose.yaml` gives the camera a `tmpfs` at `/photos` of `CAMERA_PHOTOS_SIZE` (300 MB), and `pi.min_free_mb` on the Pi
is 30. With that, measured:

- **The camera writes nothing to the card.** With the real camera, the whole Pi wrote 34 MB an hour; the app 6 of it.
- With the full desk picture, the app wrote 27 to 30 MB an hour (each picture's line in `detections.jsonl`, the object
  states and the trace pictures). The rest was Docker itself: over the hour, `containerd` 9 MB and `docker` 3.5 MB an
  hour; the whole of `system.slice` 39 MB an hour, which includes the app.
- `event_log.compact_every_min` wasn't raised: compaction turns `detections.jsonl` into a few interval lines and deletes
  it, so waiting longer wouldn't write less.
- A cleanup with the real camera deleted 1800 pictures and left 299 of 300 MB free. A full desk picture is 64 to 85 KB,
  so 10 minutes of them is about 120 to 150 MB.
- **The tmpfs counts towards the camera's memory limit** (512 MB). The camera's peak was 209 MB with pictures in memory.
  `tests/unit/test_pi_settings_file.py` checks that 10 minutes of busy pictures plus `min_free_mb` fit in
  `CAMERA_PHOTOS_SIZE`, and that it plus the program fit under `CAMERA_MEMORY_LIMIT`.
- The old `photos` volume isn't used any more. After the update, **on the Pi**, `docker volume rm desktalk_photos`
  frees its space (it holds only pictures that would have been deleted anyway).

## The README commands that hadn't been tried

- `docker compose exec detector python -c "import urllib.request; ..."` (in `raspberry_pi/README.md` and the detector's
  README): **works as written**, from a terminal on the Pi.
- `docker compose run --rm camera python3 -m raspberry_pi.camera --self-test` (camera README): **failed**,
  `open /dev/pts/ptmx: no such file or directory`. `docker compose run` gives the container a terminal, and the camera
  container has its own empty `/dev/pts`. With `-T` (no terminal) it works: "All good.", exit 0. The README now says
  `docker compose run --rm -T camera ...`.

## Image sizes

`desktalk-detector` 4.4 GB, `desktalk-app` 1.51 GB, `desktalk-camera` 1.43 GB. Easy wins I found, not made here:

- **uv's download cache is left in the images**: 1.3 GB in `/root/.cache/uv` in the detector image, 246 MB in the app
  image. `uv sync --no-cache` (or `ENV UV_NO_CACHE=1`) in both Dockerfiles would drop it. A build cache mount would
  keep rebuilds quick, but it needs BuildKit, and the files are meant to build without it too.
- The detector's packages are 1.5 GB: PyTorch 652 MB, polars 169 MB, scipy 118 MB, the full (not headless) OpenCV
  123 MB, pnnx 71 MB, matplotlib 37 MB. Most come with Ultralytics.
- The camera's 971 MB apt layer is mostly `python3-opencv`'s libraries (LLVM 107 MB, GDAL, VTK, FFmpeg, Open MPI),
  needed only for a USB webcam. Not easy to drop without losing that.
- Docker's build cache is 8.4 GB (1.4 GB reclaimable).

## Not checked: needs a person or a speaker

- A **real** cup put down and picked up: the delay and the 10 sips above used swapped photos, not a hand and a cup.
- A **voice** warning starting within 3 s: no USB speaker is plugged in, so nothing has been heard.
- The **24-hour** run: no restarts, no swap, temperature, `pytest -m pi` afterwards.
- How the page feels on a real phone while detecting: only the times above were measured.
- The camera at the desk: it still points at the ceiling.

## Things to watch

- **The detector's memory grew** from about 520 MB to 1174 MB during the hour with a full desk picture, and stayed at
  1129 MB afterwards with the ceiling picture (all of it the program's own memory, not file cache). It didn't go higher
  in the 12 minutes after, so it may have levelled off, but watch it in the 24-hour run: `DETECTOR_MEMORY_LIMIT` is 2 GB.
- **A false "cat"** 5 times in 12 minutes of ceiling pictures with the chosen model. A pet needs 0.8 confidence in 5 of
  7 pictures (`alerts.pet_*`), so single ones shouldn't warn, but it's worth watching.
- **The real data folder got an hour of the full desk picture.** I meant the 1-hour row to use the real camera, but the
  camera program keeps `pi.camera` until it restarts, so it went on playing the desk picture into the real
  `data/` folder from 21:25 to 22:26 on 2026-11-02: 6 `object_state` "appeared" (bottle, phone, desk, laptop, mouse,
  person), 3 agents joining, a few nudges (breaks, phone-free, screens), trace pictures in `data/trace_photos/`, and
  that day's habits file. Nothing was deleted to undo it. The 1-hour numbers are still good (a full desk is more work
  than the ceiling), but they aren't the real camera's.
- Until the next `update.sh`, the Pi runs the chosen settings from `raspberry_pi/mine/settings.mine.yaml`, and its
  pictures are still on the SD card (the compose file with the tmpfs isn't on the Pi yet).
