# YOLO benchmark

Finds the best YOLO model for the Raspberry Pi. It runs every combination in
[`config/benchmark.yaml`](../config/benchmark.yaml) on this computer and on the
Pi, measures how fast and how accurate each one is, and keeps the results in
[`results/`](results/) in git, so we can compare runs over time.

What gets tried (change it in `config/benchmark.yaml`):

| | |
|---|---|
| models | yolo11n, yolo11s, yolo11m |
| picture sizes | 320, 480, 640 |
| formats and precisions | pt fp32 · ncnn fp32, fp16 · onnx fp32, int8 |

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
| `throttled` | the Pi's slow-down flags, e.g. `0x0` = fine, `0x50005` = too hot or low voltage |
| `detections_total`, `detections_per_picture`, `mean_confidence` | what it found on the timed pictures |
| `map50_95`, `map50`, `box_precision`, `box_recall` | accuracy on 128 COCO photos with the right answers marked |
| `model_size_mb` | size of the model file |

Each combination runs in its own process, so memory and CPU numbers don't mix.
On the Pi, each one waits until the CPU is below 60 °C first, so a hot Pi doesn't
make later combinations look slower. Results are saved after every combination; if
a run stops, `--resume` carries on where it left off. A combination that fails is
written down as `failed` with the reason, and the run goes on.

`results/README.md` shows the latest run per device, the **best pick** (the fastest
combination that is fast enough for 3 pictures a second and within 90% of the best
accuracy), and the history of all runs.

Note on accuracy: the COCO photos are from the set these models were trained on,
so the numbers are higher than on new photos. They are good for comparing
combinations with each other, not as a real-world score. yolo11 models know the
80 COCO kinds of objects, not the 365 the app's current model knows (no keys,
earphones or pens).

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

3. **On your Mac**, copy the converted models over, so the Pi times exactly the same files
   (otherwise the Pi converts them itself, which is slow). Put in your Pi's address:

   ```bash
   uv run deskbench prepare && rsync -av ../models/bench/ pi@raspberrypi.local:desk-talk/models/bench/
   ```

4. **On the Pi**, build the image (once, and again only when `uv.lock` changes; takes a while):

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

## Tests

```bash
uv run --group dev pytest
```

```bash
uv run --group dev ruff check .
```
