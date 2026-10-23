# YOLO benchmark

Finds good YOLO models for the Raspberry Pi. It runs every combination in
[`config/benchmark.yaml`](../config/benchmark.yaml) on this computer and on the
Pi, measures how fast and how accurate each one is, and keeps the results in
[`results/`](results/) in git, so we can compare runs over time.

The **desk test** (below) scores every model on desk photos for the eight kinds of
things on Sam's desk, and the report names **three picks to try**: fast enough on
the Pi, and best on the desk. Why it works this way: [desk-test-design.md](desk-test-design.md).

What gets tried (change it in `config/benchmark.yaml`):

| | |
|---|---|
| yolo11 n, s, m | 80 COCO kinds of objects · pt fp32, ncnn fp32 and fp16, onnx fp32 and int8 |
| yolo26 n, s, m | 80 COCO kinds, newer · ncnn fp32 and fp16 |
| yolo26-objv1 n, s, m | 365 Objects365 kinds, incl. earphones (the app's kind) · ncnn fp32 and fp16 |
| yoloe-26 n, s, m | finds the words it's given (the desk kinds) · ncnn fp32 and fp16 |
| picture sizes | 320, 480, 640, for every model |

What gets measured for each combination:

| Column | Meaning |
|---|---|
| `fps` | pictures a second |
| `inference_ms_p50`, `_p95` | time the model itself takes per picture: typical, and slowest 5% |
| `total_ms_p50`, `_p95` | the whole detection call per picture (resize + model + tidy up) |
| `total_time_s` | time for all the timed pictures |
| `load_time_s` | loading the model plus the first detection |
| `cpu_cores_used`, `cpu_percent` | average CPU use (`cpu_percent` = share of all cores) |
| `peak_memory_mb` | most memory the process used |
| `temp_start_c`, `temp_max_c`, `temp_end_c` | CPU temperature (Pi only; macOS doesn't allow reading it) |
| `cpu_mhz_min`, `cpu_mhz_top` | the CPU's lowest speed while timing, and its full speed (Pi only), as Linux reports them. Not checked yet: on a Pi 5 the firmware may slow the CPU without Linux showing it, so trust `temp_max_c` too (the Pi 5 slows down from about 80 °C) |
| `detections_total`, `detections_per_picture`, `mean_confidence` | what it found on the timed pictures |
| `map50_95`, `map50`, `box_precision`, `box_recall` | accuracy on 128 COCO photos with the right answers marked (COCO kinds only: yolo11, yolo26) |
| `model_size_mb` | size of the model file |

Each combination runs in its own process, so memory and CPU numbers don't mix.
On the Pi, each one waits until the CPU is below 60 °C first, so a hot Pi doesn't
make later combinations look slower. Results are saved after every combination; if
a run stops, `--resume` carries on where it left off. A combination that fails is
written down as `failed` with the reason, and the run goes on.

`results/README.md` shows the three picks and the desk test, then each device's
results (all its runs together, newest row of each combination), its **best pick**
(the fastest combination that is fast enough for 3 pictures a second and within
90% of the best COCO accuracy, in a format the Pi's detector service runs: ncnn or
pt, set by `report.pick_formats`), and the history of all runs.

Note on the COCO accuracy: the 128 photos are from the set these models were
trained on, so the numbers are higher than on new photos. They are good for
comparing combinations with each other, not as a real-world score. The desk test
is the better guide.

## On this computer (Mac)

**On your Mac**, in `desk-talk/benchmarks`:

```bash
uv sync
```

```bash
uv run deskbench run --quick --models yolo11n
```

That quick check takes a few minutes and writes to `data/bench/quick-results/`
(not git). The full run:

```bash
uv run deskbench run
```

## On the Raspberry Pi

Needs 64-bit Raspberry Pi OS and about 3 GB free.

1. **On the Pi**, install Docker (once), then log out and back in:

   ```bash
   curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
   ```

2. **On the Pi**, get the code (once, if `desk-talk` isn't there yet), then the latest version:

   ```bash
   git clone https://github.com/randomnoobi/desk-talk.git ~/desk-talk
   ```

   ```bash
   cd ~/desk-talk && git pull
   ```

3. **On your Mac**, make the models and copy them over, so the Pi times exactly the same
   files. The Pi can't make the yoloe ones itself (they need the `words` packages), and
   converting the others on the Pi is slow. Put in your Pi's address:

   ```bash
   uv sync --group words && uv run deskbench prepare && rsync -av ../models/bench/ pi@raspberrypi.local:desk-talk/models/bench/
   ```

4. **On the Pi**, build the image (once, and again whenever `uv.lock` changes; takes a while):

   ```bash
   cd ~/desk-talk/benchmarks && docker compose build
   ```

5. **On the Pi**, a quick check, then the full run. The full run can take a few
   hours, so it runs in the background and keeps going if you close the SSH window:

   ```bash
   docker compose run --rm bench run --quick --models yolo11n
   ```

   ```bash
   nohup docker compose run --rm -T bench run > ~/bench.log 2>&1 &
   ```

   ```bash
   tail -f ~/bench.log
   ```

   To time only some kinds of models (for example the newer ones, after a run that
   already timed yolo11), add `--kinds`:

   ```bash
   nohup docker compose run --rm -T bench run --kinds yolo26 yolo26-objv1 yoloe-26 > ~/bench.log 2>&1 &
   ```

   If it stops (power cut, reboot), carry on with the same results file:

   ```bash
   docker compose run --rm bench run --resume results/pi/<the run>.csv
   ```

## Recording the results on GitHub

Each run adds `results/<device>/<run>.csv` (the numbers) and `<run>.json` (the
machine, software versions, commit and settings it ran with).

**On your Mac**, fetch the Pi's results, rebuild the summary and commit:

```bash
rsync -av pi@raspberrypi.local:desk-talk/benchmarks/results/pi/ results/pi/
```

```bash
uv run deskbench report && git add results && git commit -m "Add Pi benchmark results" && git push
```

## Desk test

Scores every combination on desk photos, for the eight kinds of things on Sam's desk
(`desk.kinds` in the config): book, water bottle, mouse, laptop, food, AirPods, phone,
trash. Runs on the Mac only: a model gives the same answers on any computer, so only
speed needs the Pi.

- **Public photos:** about 240 desk photos from LVIS (COCO val2017 photos, which the
  COCO models never trained on), sorted into scenes: covering, busy, small things,
  food, trash, tidy. The list and the right answers are in
  [`desk/desk-set.json`](desk/desk-set.json); the photos themselves are downloaded to
  `data/bench/desk/lvis/` (not in git).
- **Your own photos:** `data/bench/desk-photos/` (not in git), with `labels.yaml`
  saying what's in each one.
- **Score:** for each picture and kind, did the model find it? A kind's score = found
  rate × (1 − false alarms), and the desk score is the average over the kinds. "Found"
  uses the app's own rule for how sure a model must be (`agents.min_confidence`,
  0.55 for bottles). A kind a model has no name for (yolo11 and AirPods) scores 0.

**On your Mac**, in `desk-talk/benchmarks` (the first line only once, for yoloe):

```bash
uv sync --group words
```

```bash
uv run deskbench desk prepare
```

```bash
uv run deskbench desk run
```

`desk prepare` chooses the photos and downloads them (about 150 MB the first time).
On another computer, `uv run deskbench desk prepare --keep-set` downloads the same
photos without choosing again. `desk run` takes about 15 minutes for all 99
combinations, writes `results/desk/<run>.csv`, and rebuilds `results/README.md`. The
three picks there use the Pi's speed, so models not timed on the Pi yet are listed
but can't be picked.

What the desk CSV has, per combination:

| Column | Meaning |
|---|---|
| `desk_score` | average of the kinds' scores, on the public photos |
| `desk_score_low` | the same, counting weaker finds too (confidence 0.25) |
| `your_desk_score` | the desk score on your own photos |
| `score_<kind>`, `found_<kind>`, `false_<kind>`, `there_<kind>` | each kind's score, found rate, false alarms, and how many photos have it |
| `scene_<scene>` | the desk score in each scene |
| `pictures` | public photos used |

### Adding photos of your own desk

1. **On your Mac**, start the app (`python server.py` in `desk-talk`), and on the Pi
   the camera command it prints.
2. **On your Mac**, watch the live picture at `http://localhost:5000/api/video_feed_raw`.
3. **On your Mac**, in `desk-talk`, save a picture each time you press Enter:

   ```bash
   mkdir -p data/bench/desk-photos
   ```

   ```bash
   n=1; while true; do f=desk_$(printf %02d $n).jpg; echo -n "Set up picture $n, then press Enter (Ctrl+C to stop) "; read; if curl -sf http://localhost:5000/api/latest_frame_raw/0 -o data/bench/desk-photos/$f; then echo "saved $f"; n=$((n+1)); else echo "no picture yet: is the Pi sending?"; fi; done
   ```

   Start at a higher `n` to add to pictures you already have.
4. List what's in each picture in `data/bench/desk-photos/labels.yaml`, using the kind
   names (anything not listed counts as not there):

   ```yaml
   desk_07.jpg: [laptop, phone, water bottle]
   ```

## Tests

```bash
uv run --group dev pytest
```

```bash
uv run --group dev ruff check .
```
