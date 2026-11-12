# Pi Stats Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to carry out this plan, following "How to work" in `AGENTS.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new "Pi" tab on the developer page that shows every system number the Pi can give, live and over the last half hour, in charts.

**Architecture:** The `app` container on the Pi reads Linux's own files (`/proc`, `/sys`) every `system_stats.every_sec` and keeps the last `system_stats.keep_min` minutes in memory (`StatsHistory`, a `PeriodicWorker`). It answers `GET /api/system/stats?since=` on the Wi-Fi, like `/api/health`. The developer page only runs on a computer (it's off on the Pi), and the app lets no other web site read it (`web.cors_origins: []`), so the computer's server fetches the Pi's reply for its page: `GET /api/dev/pi_stats?since=` passes the request on to `http://{dev.pi_address}/api/system/stats`. The tab draws the samples with uPlot, which the page already uses.

**Tech Stack:** Python 3.12 standard library for reading (no psutil: it can't read the Pi's network from inside a container anyway), FastAPI, React 18 + TypeScript + uPlot 1.6.32 (already in `dev_ui/package.json`).

**Spec:** the user's request on 2026-11-11: "a new tab in the dev UI ... all system stats of the Raspberry Pi, including CPU usage, RAM usage, disk space, I/O, temperature, fan speed etc (anything else you can think of) ... any suitable visualization framework", and "just build a stats visualization page for all possible system stats you can collect from pi". No questions were asked (the user opted out).

## What the real Pi has (read over `ssh pi` on 2026-11-11)

Raspberry Pi 5 Model B Rev 1.1, kernel 6.18.50+rpt-rpi-2712, 4 cores, `CLK_TCK` 100, page size **16384** (so use `os.sysconf`, never 4096).
- `/sys/class/thermal/thermal_zone0` type `cpu-thermal` (55100 = 55.1 °C); `cooling_device0` type `pwm-fan`, state 1/4.
- `/sys/class/hwmon/hwmon0` `cpu_thermal` temp1; `hwmon1` `rp1_adc` in1..in4 (millivolts) and temp1; `hwmon2` `pwmfan` fan1_input (3416 RPM), pwm1 (75 of 255); `hwmon3` `rpi_volt` in0_lcrit_alarm (1 = the power supply is too weak right now; this Pi has one too weak).
- No `get_throttled` file on this kernel. `vcgencmd` needs root's `/dev/vcio`: not used.
- No `/proc/pressure`. `/proc/diskstats` has `mmcblk0` and its partitions. `/proc/net/dev` on the Pi has `wlan0`, `eth0`, `lo`, `docker0`, `br-*`, `veth*`. `/proc/net/wireless` has `wlan0` quality 46, level -64.
- Inside the `app` container, `/sys` is the Pi's (all the sensors show), `/proc/stat`, `/proc/meminfo`, `/proc/diskstats`, `/proc/loadavg` are the whole Pi's, but `/proc/net/dev` and `/proc/<pid>` are only the container's own. With the Pi's `/proc` mounted read-only at `/host/proc`, `/host/proc/1/net/dev` is the Pi's network and `/host/proc/<pid>` every process. User 1000 (the app's user) may read both (checked).

## Global Constraints

- `AGENTS.md` rules: every number in `config/settings.yaml` with a plain comment and a typed field in `desktalk/config/app.py`; wording in `config/prompts.yaml` (+ `BLANKS`); `bootstrap.py` is the only place that knows real classes; routes in `desktalk/web/routers/` with a characterization case; `Clock` for the time; `logging`, never `print`; plain words.
- `desktalk/core/` and `desktalk/domain/` import no OS-reading, web or AI code: the file reading is an adapter (`desktalk/adapters/system/`).
- Tests never touch the real `/proc`, `/sys` or the network: a folder made to look like them (`tmp_path`), and the fakes.
- A reading that is missing (no fan, no Wi-Fi, a Mac) is left out or `None`, never an error. A file that can't be read or parsed makes that one number missing, not the whole reading.
- The developer page keeps no numbers of its own: how often to ask comes from the reply's `every_sec`, the hot lines from `GET /api/dev/settings` (`health.pi_cpu_warn_c` and `health.pi_cpu_bad_c`, already there for the System tab).
- Command lines shown are cut to `system_stats.command_chars`.

## The reply (the one shape every task uses)

`GET /api/system/stats?since=<seconds since 1970>` (samples newer than `since`; all when left out):

```json
{"collecting": true, "every_sec": 2, "keep_min": 30,
 "info": {"model": "Raspberry Pi 5 Model B Rev 1.1", "kernel": "6.18.50+rpt-rpi-2712", "cores": 4},
 "samples": [{
   "at": 1762900000.0, "uptime_sec": 86400.0,
   "cpu": {"busy_pct": 53.1, "user_pct": 40.0, "system_pct": 10.0, "iowait_pct": 1.0, "other_pct": 2.1,
           "cores_pct": [50.0, 60.0, 45.0, 57.4], "freq_mhz": [2400.0, 2400.0, 1500.0, 2400.0], "max_freq_mhz": 2400.0,
           "load": [2.44, 2.57, 2.62], "running": 3, "processes": 481},
   "memory": {"total_bytes": 0, "used_bytes": 0, "available_bytes": 0, "cached_bytes": 0, "buffers_bytes": 0,
              "free_bytes": 0, "swap_total_bytes": 0, "swap_used_bytes": 0},
   "temps": [{"name": "cpu-thermal", "c": 55.1}],
   "fans": [{"name": "pwmfan", "rpm": 3416, "pwm_pct": 29.4}],
   "cooling": [{"name": "pwm-fan", "state": 1, "max_state": 4}],
   "voltages": [{"name": "rp1_adc in1", "v": 1.477}],
   "under_voltage": true, "throttled": "",
   "disks": [{"label": "data folder", "path": "/desk-talk/data", "total_bytes": 0, "used_bytes": 0, "free_bytes": 0}],
   "disk_io": [{"name": "mmcblk0", "read_bps": 0.0, "write_bps": 0.0, "reads_per_sec": 0.0, "writes_per_sec": 0.0,
                "busy_pct": 0.0}],
   "network": [{"name": "wlan0", "rx_bps": 0.0, "tx_bps": 0.0, "rx_errors": 0, "tx_errors": 0, "rx_drops": 0,
                "tx_drops": 0}],
   "wifi": [{"name": "wlan0", "quality": 46.0, "signal_dbm": -64.0}],
   "top_processes": [{"pid": 1234, "name": "python", "command": "python -m desktalk_detector", "cpu_pct": 95.0,
                      "memory_bytes": 0}]
 }]}
```

`collecting: false` (with `samples: []` and `info: {}`) when `system_stats.enabled` is off. `under_voltage` is `null` when there's no `rpi_volt`. `cpu_pct` of a process is of one core, like `top` (so up to 100 × cores). The first reading after a start makes no sample: rates need two readings.

`GET /api/dev/pi_stats?since=` on the computer: the Pi's reply as it is, plus `"address": "<dev.pi_address>"`. 502 `{"error": prompts.dev.pi_stats_unreachable}` when the Pi doesn't answer or doesn't send that shape.

## Review Focus

1. **A counter that goes down** (the Pi rebooted between two readings, a Wi-Fi adapter reset, a process ended and its pid was reused): no negative rates. Rates clamp at 0, and a process whose start time changed counts as new. Test in Task 3.
2. **Two readings at the same moment** (elapsed 0, or a clock step back): no division by zero. Skip that sample. Test in Task 3.
3. **A process that ends while it's being read** (`/proc/<pid>` gone between the listing and the read): skipped, not an error. Test in Task 2.
4. **The Pi is off or answers slowly** while the tab is open: the computer's route gives up after `dev.pi_stats_timeout_sec` and says so in plain words; the tab keeps its old charts and shows the problem. Tests in Tasks 4 and 5.
5. **The tab is open for hours**: the page keeps at most `keep_min × 60 / every_sec` samples, so it doesn't grow forever, and asks only for samples newer than its last one. Test in Task 5.

---

### Task 1: Shared shapes, settings, words and fakes (done first, by the lead)

**Files:**
- Create: `desktalk/domain/system_stats.py`; export from `desktalk/domain/__init__.py`
- Modify: `desktalk/ports.py` (new section "System stats": `SystemStatsReader`, `SystemStats`, `PiStatsLink`)
- Modify: `config/settings.yaml` (new `system_stats:` section; `dev.pi_address`, `dev.pi_stats_timeout_sec`), `desktalk/config/app.py` (`SystemStatsSettings`, `StatsDisk`, `DevSettings` fields, `AppConfig.system_stats`), `raspberry_pi/settings.pi.yaml` (`system_stats: enabled: true, host_proc_folder: /host/proc`)
- Modify: `config/prompts.yaml` (`dev.pi_stats_unreachable` with `{address}` and `{problem}`), `desktalk/config/prompts.py` (`BLANKS`, `DevWords`)
- Create: `tests/fakes/system.py` (`FakeStatsReader`, `FakeStats`, `FakePiStatsLink`), export from `tests/fakes/__init__.py`
- Create: `tests/contracts/system_stats_contract.py`; `tests/unit/test_domain_system_stats.py`

**Produces:**
- `desktalk.domain`: `CpuTimes(user, system, iowait, idle, other)` in ticks with `.total` and `.busy`; `MemoryNow`, `Named(name, value)`, `Fan(name, rpm, pwm_pct)`, `Cooling(name, state, max_state)`, `DiskSpace(label, path, total_bytes, used_bytes, free_bytes)`, `DiskCounters(name, read_bytes, write_bytes, reads, writes, busy_ms)`, `NetCounters(name, rx_bytes, tx_bytes, rx_errors, tx_errors, rx_drops, tx_drops)`, `WifiNow(name, quality, signal_dbm)`, `ProcessTicks(pid, started, name, command, ticks, memory_bytes)`, `StatsInfo(model, kernel, cores)`, `RawReading(at, uptime_sec, cpu, cores, freq_mhz, max_freq_mhz, load, running, processes, memory, temps, fans, cooling, voltages, under_voltage, throttled, disks, disk_io, network, wifi, process_ticks, ticks_per_sec, info)`, and the sample types `StatsSample` (+ `CpuNow`, `DiskRate`, `NetRate`, `TopProcess`) with `to_dict()` giving exactly the JSON above. Every field has a default so a reading with nothing in it is valid.
- `ports.SystemStatsReader.read(now: float) -> RawReading` (never raises for a missing file).
- `ports.SystemStats.history(since: float | None) -> dict[str, Any]` (the reply above) and it is a `PeriodicWorker` (`name`, `tick(now) -> float`).
- `ports.PiStatsLink.fetch(since: float | None) -> dict[str, Any]`; raises `desktalk.domain.StatsUnavailable(problem: str)`.
- Settings: `config.system_stats.{enabled, every_sec, keep_min, proc_folder, sys_folder, host_proc_folder, disks, top_processes, command_chars, skip_networks, skip_disks}`, `config.dev.pi_address`, `config.dev.pi_stats_timeout_sec`.
- Fakes: `FakeStatsReader(readings: list[RawReading])` gives them one by one, then repeats the last; `FakeStats(reply: dict)`; `FakePiStatsLink(reply: dict | None = None, problem: str = '')` that raises `StatsUnavailable(problem)` when `problem` is set and records each `since` in `.asked`.
- Contract suites: `SystemStatsReaderContract` (read() gives a `RawReading` with `at == now`), `SystemStatsContract` (history before any tick is the reply shape with `samples == []`; `since` keeps only newer samples), `PiStatsLinkContract` (fetch gives a dict with `samples`, or raises `StatsUnavailable`). The fakes each get a test class.

- [x] Write `tests/unit/test_domain_system_stats.py`: `StatsSample().to_dict()` has every key of the JSON above; `CpuTimes(user=40, system=10, iowait=1, idle=47, other=2).busy == 52` and `.total == 100`. Run, watch it fail, write the types, run again.
- [x] Write the fakes' contract test classes, watch them fail, write `ports.py`, the fakes and the contracts.
- [x] Add the settings and words; `uv run pytest tests/unit/test_config*.py tests/unit/test_prompts*.py -q` passes.
- [x] Commit: "Add the shapes, settings and fakes for the Pi's system numbers".

### Task 2: Reading `/proc` and `/sys` (subagent A)

**Files:**
- Create: `desktalk/adapters/system/__init__.py`, `desktalk/adapters/system/proc_reader.py`
- Test: `tests/unit/test_adapters_system_proc_reader.py`, `tests/contracts/test_system_stats_reader_contract.py` (a `ProcReader` class running `SystemStatsReaderContract`)

**Interfaces:** Consumes Task 1's domain types and `SystemStatsSettings`. Produces `ProcReader(settings: SystemStatsSettings, *, root: Path = Path('/'), disk_paths: Sequence[tuple[str, Path]], ticks_per_sec: int | None = None, page_bytes: int | None = None)`, a `SystemStatsReader`. The three folders in the settings are taken under `root` (tests pass `tmp_path`). `ticks_per_sec`/`page_bytes` default to `os.sysconf('SC_CLK_TCK')` / `os.sysconf('SC_PAGE_SIZE')`.

What it reads (one small function each, each giving its empty value when its file is missing or garbled):
- `proc_folder/stat`: the `cpu` line and each `cpuN` line → `CpuTimes` (user = user + nice; other = irq + softirq + steal); `proc_folder/loadavg` → load, running, processes; `proc_folder/uptime`; `proc_folder/meminfo` (kB × 1024; used = total − available); `proc_folder/sys/kernel/osrelease`; `proc_folder/diskstats` for names that are folders in `sys_folder/block/` and don't start with any of `skip_disks` (sectors × 512; busy_ms = field 13).
- `sys_folder/firmware/devicetree/base/model` (strip `\0`); `sys_folder/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq` and `cpuinfo_max_freq` (kHz → MHz); `sys_folder/class/thermal/thermal_zone*/{type,temp}`; `sys_folder/class/thermal/cooling_device*/{type,cur_state,max_state}`; `sys_folder/class/hwmon/hwmon*/`: `name`, `temp*_input` (m°C, named `"<name>"` or `"<name> tempN"` when it has more than one), `fan*_input` with `pwm*` (÷255 ×100), `in*_input` (mV → V, `"<name> inN"`), `rpi_volt`'s `in0_lcrit_alarm` → `under_voltage`; the throttle file (same glob as `raspberry_pi/camera/health.py`'s `THROTTLED_FILE`) → `throttled` as `hex()`, else `''`. Thermal-zone temps and hwmon temps both go in `temps`, skipping a hwmon temp whose value equals a thermal zone's (the Pi's `cpu_thermal` hwmon is the same sensor as `cpu-thermal`).
- `host_proc_folder/1/net/dev` for names not starting with any of `skip_networks`; `host_proc_folder/1/net/wireless` (`quality` and `level`, trailing dots stripped).
- `host_proc_folder/<pid>/stat` (utime + stime = fields 14+15, start time field 22, split after the last `)`), `.../statm` (resident pages × `page_bytes`), `.../cmdline` (`\0` → space, cut to `command_chars`; kernel threads with an empty one get `[name]`).
- `shutil.disk_usage` for each of `disk_paths` that exists.

- [ ] Write the tests first against a fake tree built in `tmp_path` from the real Pi lines above (a test per file kind, plus: an empty `tmp_path` gives a `RawReading` with everything empty and `under_voltage is None`; a `<pid>` folder whose `stat` is missing is skipped; a garbled `meminfo` line is skipped; partitions `mmcblk0p1` and `loop0` are left out; `veth*` and `lo` are left out). Watch them fail.
- [ ] Write `ProcReader`. Run `uv run pytest tests/unit/test_adapters_system_proc_reader.py tests/contracts/test_system_stats_reader_contract.py -q`, `uv run ruff check desktalk/adapters/system tests/unit/test_adapters_system_proc_reader.py`.
- [ ] Don't commit: the lead commits.

### Task 3: Turning readings into samples, and the history (subagent B)

**Files:**
- Create: `desktalk/core/system/__init__.py`, `desktalk/core/system/stats_history.py`
- Test: `tests/unit/test_core_system_stats_history.py`, `tests/contracts/test_system_stats_contract.py` (`StatsHistory` running `SystemStatsContract`)

**Interfaces:** Consumes `SystemStatsReader`, `Clock`, `SystemStatsSettings`, the domain types. Produces:
- `sample_between(before: RawReading, after: RawReading, top_processes: int) -> StatsSample | None` (pure; `None` when `after.at <= before.at`).
- `StatsHistory(reader: SystemStatsReader, settings: SystemStatsSettings)`: `name = 'system stats'`; `tick(now) -> float` reads, makes a sample from the reading before (if any), keeps samples in a `deque(maxlen=round(keep_min * 60 / every_sec))`, returns `every_sec`; `history(since)` gives the reply (with `collecting = settings.enabled`, `info` from the latest reading's `StatsInfo`). Thread safe (a lock): the web layer reads while the job writes.

Rates: `(after − before) / (after.at − before.at)`, clamped at 0. CPU percents: each part's tick change ÷ total tick change × 100 (0 when total didn't change); cores the same, matched by position. Disk busy: busy_ms change ÷ (elapsed × 1000) × 100, at most 100. Processes: matched by `(pid, started)`; one with no match before is left out of that sample; `cpu_pct` = tick change ÷ (elapsed × ticks_per_sec) × 100; top `top_processes` by `cpu_pct`, ties by memory. Network errors and drops are the counters as they are now (since boot).

- [ ] Tests first, with `FakeStatsReader` and hand-made `RawReading`s: two readings 2 s apart give the right rates; a counter that went down gives 0 (Review Focus 1); equal `at` gives `None` and `tick` keeps no sample (Review Focus 2); a reused pid with a new `started` isn't in the top list; the deque keeps only `keep_min × 60 / every_sec`; `history(since)` filters; `tick` returns `every_sec`. Watch them fail.
- [ ] Write it. Run `uv run pytest tests/unit/test_core_system_stats_history.py tests/contracts/test_system_stats_contract.py -q` and ruff on the new files.
- [ ] Don't commit: the lead commits.

### Task 4: The computer's line to the Pi (subagent C)

**Files:**
- Create: `desktalk/adapters/dev/pi_stats_link.py`
- Test: `tests/unit/test_adapters_dev_pi_stats_link.py`, `tests/contracts/test_pi_stats_link_contract.py`

**Interfaces:** Produces `HttpPiStatsLink(address: str, timeout_sec: float, *, opener: Callable[[str, float], bytes] | None = None)`, a `PiStatsLink`. URL: `http://{address}/api/system/stats`, plus `?since=<since>` when given. The default `opener` uses `urllib.request.urlopen(url, timeout=...)` and reads the body. Any `OSError`/`urllib.error.URLError`/timeout, a non-JSON body, or JSON that isn't an object with a `samples` list → `StatsUnavailable(<plain short problem>)`.

- [ ] Tests first with a fake `opener` (no network): the URL with and without `since`; good JSON comes back as a dict; a raised `URLError` and a `TimeoutError` become `StatsUnavailable`; `b'not json'` and `b'[]'` too. The contract class passes the suite with a fake opener. Watch them fail.
- [ ] Write it. Run its tests and ruff.
- [ ] Don't commit: the lead commits.

### Task 5: The Pi tab on the developer page (subagent D)

Load the `dataviz` skill before writing chart code.

**Files:**
- Create: `dev_ui/src/tabs/Pi.tsx`, `dev_ui/src/tabs/Pi.test.tsx`, `dev_ui/src/StatsChart.tsx` (one uPlot time chart: several lines, a unit, an optional dashed level line, loaded with `import('uplot')` like `SmoothingChart.tsx`), `dev_ui/src/piStats.ts` + `dev_ui/src/piStats.test.ts` (pure helpers)
- Modify: `dev_ui/src/api.ts` (types for the reply above, `getPiStats(since?: number)` → `/api/dev/pi_stats`), `dev_ui/src/App.tsx` (tab `{ id: 'pi', name: 'Pi', Tab: Pi }` after `system`), `dev_ui/src/App.test.tsx` if it lists the tabs, `dev_ui/README.md`

**Interfaces:** Consumes the reply above (with `address`) and `DevSettings.health.pi_cpu_warn_c` / `pi_cpu_bad_c`. `piStats.ts` produces `mergeSamples(old: PiSample[], fresh: PiSample[], keep: number): PiSample[]` (by `at`, no duplicates, newest `keep`), `keepCount(reply): number` (= `Math.round(keep_min * 60 / every_sec)`), `series(samples, pick): (number | null)[]` for lines that come and go (a fan that appears later), `formatBytes`, `formatRate`.

The tab, top to bottom:
1. A header card: the model, kernel, cores, how long the Pi has been up, when the last sample came, "asking again every N s", and warnings in plain words: "The power supply is too weak right now" (`under_voltage`), "The processor is hot: 82 °C" (amber at `pi_cpu_warn_c`, red at `pi_cpu_bad_c`), the Pi can't be reached (the 502 words), "The Pi isn't collecting its numbers (system_stats.enabled is off there)".
2. A row of big numbers (stat tiles): CPU %, memory used %, data folder used %, hottest temperature, fan RPM, Wi-Fi signal dBm, load (1 min).
3. Charts: CPU (busy, user, system, I/O wait); each core; CPU speed per core (MHz, max as a level line); load 1/5/15 min; temperatures (all sensors; `pi_cpu_warn_c` and `pi_cpu_bad_c` as level lines); fan RPM and fan power %; memory (used, cached, available) and swap used; disk read and write (bytes/s) and disk busy %; network received and sent (bytes/s) per interface; Wi-Fi signal (dBm); voltages.
4. Tables: disk space per disk with a bar; the top processes (name, command, CPU %, memory) from the newest sample; cooling states; network errors and drops.
Poll with `usePoll`, every `every_sec` from the last reply (`settings.poll_sec` until the first one), asking `since` = the newest `at` it has.

- [ ] Tests first: `piStats.test.ts` for `mergeSamples` (dedupes, keeps order, caps at `keep`: Review Focus 5) and `series` (null for a missing name); `Pi.test.tsx` with `fetch` mocked like the other tab tests: it shows the model and the tiles from a reply; it shows the power warning when `under_voltage` is true; on a 502 it shows the error words and keeps the charts it had (Review Focus 4); `collecting: false` shows the "isn't collecting" line. Watch them fail (`npm test` in `dev_ui/`).
- [ ] Write it. `npm test` and `npm run build` in `dev_ui/` pass.
- [ ] Don't commit: the lead commits.

### Task 6: Connecting it all (the lead, after Tasks 2–4)

**Files:**
- Modify: `desktalk/bootstrap.py` (`build(..., stats_reader: SystemStatsReader | None = None, pi_stats_link: PiStatsLink | None = None)`; `ProcReader` from `config.system_stats` with `disk_paths=[(d.label, config.resolve(d.path)) for d in disks]`; `StatsHistory`; a `DeskTalk.system_stats: SystemStats` field; the job in `background_jobs()` only when `system_stats.enabled`; `HttpPiStatsLink(dev.pi_address, dev.pi_stats_timeout_sec)` as `DeskTalk.pi_stats_link: PiStatsLink | None` when `dev.enabled`)
- Create: `desktalk/web/routers/system.py` (`GET /api/system/stats`, `since: float | None = None`); include it in `desktalk/web/app.py` with the main routers (not the phone's)
- Modify: `desktalk/web/routers/dev.py` (`GET /api/dev/pi_stats`, run in the thread pool; 502 with the words on `StatsUnavailable`)
- Modify: `tests/characterization/cases.py` (`system-stats` 200; `dev-pi-stats` 200, `dev-pi-stats-unreachable` 502, `dev-pi-stats-other-computer` 403), `tests/characterization/conftest.py` (pass `FakePiStatsLink` and `FakeStatsReader`), new snapshots
- Modify: `raspberry_pi/compose.yaml` (app: `- /proc:/host/proc:ro` with a comment saying why), `tests/pi/` (a test that the Pi's `/api/system/stats` has `wlan0` in `network`, a temperature, and a process from each container)
- Modify: `docs/ARCHITECTURE.md`, `docs/API.md`, `raspberry_pi/README.md` (one line), `tests/vulture_allow.py` if needed

- [ ] Characterization cases and a route test first (`tests/unit/test_web_system_router.py`: `collecting` follows `system_stats.enabled`; `since` is passed through; the dev route returns the link's reply plus `address`, and 502 with the words). Watch them fail, wire it up, pass.
- [ ] Full checks: `uv run pytest -m "not slow and not docker and not e2e" --cov`, `uv run ruff check <changed files>`, `uv run pyright`, `uv run vulture`, `npm test` and `npm run build` in `dev_ui/`.
- [ ] Commit, push, open the PR, then `/code-review high <PR>` in a subagent; a second subagent fixes what it finds.
- [ ] The real-Pi check (`DESKTALK_PI=pi uv run pytest -m pi`) needs this branch on the Pi, which runs `pi-reliability` now: ask the user before switching it.
