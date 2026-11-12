# Pi Reliability Plan

> **For agentic workers:** follow "How to work" in `AGENTS.md`. Each task below is done by its own subagent in its own worktree and branch, cut from `pi-reliability`. Test first.

**Goal:** the Pi keeps going by itself through power cuts, a lost network, a pulled cable, a full SD card and a wrong clock, tells the user when something needs a person, and leaves logs and backups a person can use.

**Where it comes from:** the 2026-11-10 meeting notes (the list below), and a read of the code on `restore-boot-id` (PR #16).

**Start:** `pi-reliability`, which is `restore-boot-id` (main plus PR #16).

## The meeting's list, and the task that does each

| # | Asked for | Task |
|---|---|---|
| 1 | Merge the current code, handle each failure on its own, run the tests and check the old features still work | Merge PR #16 (needs the user's OK); final checks |
| 2 | A systemd service that brings everything back after a power cut; check its permissions and that it comes back after a reboot | A |
| 3 | Running state and diagnostics in the systemd journal, at most 200 MB of logs, and a simple script to pull logs | A |
| 4 | Logs rotate every 30 days | A |
| 5 | Retries wait longer each time, with jitter; a circuit breaker for failures that go on | B |
| 6 | A heartbeat and alerts, with the camera, temperature and other measures each checked on its own | C |
| 7 | Alert levels for SD card space, "the camera has no new detections" and temperature (the meeting's numbers aren't final) | C |
| 8 | Clock: keep whether the clock was synced with each saved row, look at an RTC battery, use the monotonic clock where it matters | D |
| 9 | A limit on SD card space, and a weekly backup to the computer | E |
| 10 | A runbook for camera and SD card failures that need a person | F |
| 11 | Hand tests: power cut and back, no network, a pulled cable, a full SD card, the clock set to 1990 | F (steps and checks), the user (doing them) |

## What is there already (so no task redoes it)

- Containers have `restart: unless-stopped`, health checks and `raspberry_pi/health_check.py`; `tests/pi/test_real_pi.py::assert_started_by_itself` checks a power cut. No systemd unit; install.sh never enables `docker.service`.
- Docker logs use `json-file` (10 MB x 3 per container); the app also keeps `data/logs/desktalk.log`. Raspberry Pi OS keeps the journal in memory only (`/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf`).
- `shared/desktalk_shared/backoff.py` doubles waits with no jitter. Breakers exist in `desktalk/adapters/llm/resilient.py` (`_Breaker`) and `raspberry_pi/camera/detector_client.py` (its own pause).
- `Lens` (`desktalk/core/chat/workers/lens.py`) speaks camera-lost, Pi-hot, Pi-slowed and detector-off warnings, with words in `config/prompts.yaml`. Nothing warns about SD card space or about a detector that stays busy and never answers. `PiHealth.disk_free_mb` measures the camera's in-memory `/photos`, not the card.
- `object_states.boot_id_file` and `clock_synced_file` exist (PR #15, #16); nothing per row says whether the clock was synced. `desktalk/runtime/scheduler.py` and `raspberry_pi/camera/cleaner.py` time their intervals with the wall clock. Nothing notices a clock years behind.
- The Pi is a Raspberry Pi 5: it has a clock chip (`/dev/rtc0`, `rpi-rtc`) and only lacks its battery (the J5 connector).
- No backup. Docker's build cache on the Pi is 13 GB and nothing prunes it. The object-state database only grows.

## Tasks

Each task names the files it owns. Shared files (`config/settings.yaml`, `desktalk/config/app.py`, `config/prompts.yaml`, `desktalk/bootstrap.py`, `raspberry_pi/compose.yaml`, `raspberry_pi/.env.example`) are touched by more than one task, each in its own section, on its own branch; they are merged one after another at the end.

### A. Start on boot and logs in the journal
- `raspberry_pi/systemd/desktalk.service` (new): runs `docker compose up -d` from the repo's `raspberry_pi/` folder as the Pi's user after `docker.service`, without waiting for the network. `install.sh` fills in the user and folder, installs it, enables it plus `docker.service` and `containerd.service`.
- `raspberry_pi/systemd/journald-desktalk.conf` (new): `Storage=persistent`, `SystemMaxUse=200M`, `MaxFileSec=30day`, `MaxRetentionSec=30day`, named to sort after Raspberry Pi OS's `40-rpi-volatile-storage.conf`. The numbers come from `.env.example`.
- `raspberry_pi/compose.yaml`: the three `logging:` blocks use the `journald` driver with a tag per container.
- `raspberry_pi/get_logs.sh` (new, **on the Pi**) and `scripts/pi_logs.sh` (new, **on your Mac**, over `ssh pi`): save the last N hours of each container's log, the boot list and the health state into one file.
- Tests: `tests/unit/test_pi_install_script.py`, `tests/unit/test_pi_compose_file.py`, new `tests/unit/test_pi_log_scripts.py`; a new `-m pi` check that the unit is enabled and the journal is persistent.

### B. Jitter and one circuit breaker
- `shared/desktalk_shared/backoff.py`: `Backoff(first, most, *, jitter=0.0, rng=None)`; jitter only shortens a wait.
- `shared/desktalk_shared/breaker.py` (new): `Breaker`, moved out of `resilient.py`, times passed in as `now`.
- Switch to them: `raspberry_pi/camera/uploader.py`, `raspberry_pi/camera/detector_client.py`, `desktalk/core/cloud/uploader.py`, `desktalk/runtime/supervisor.py`, `desktalk/adapters/llm/resilient.py`, `raspberry_pi/detector_service/desktalk_detector/manager.py`, `cloud/server/desktalk_cloud/report_job.py`.
- Settings: a jitter fraction per section, wired in `desktalk/bootstrap.py`, `raspberry_pi/camera/workers.py`, the detector's `bootstrap.py`.

### C. Health checks and alerts
- A new part behind a port in `desktalk/ports.py` that checks each measure on its own: camera pictures arriving, new detections arriving (covers a detector that stays busy), detector off, temperature (a warning and a higher level), SD card free space (measured by the app on its data folder, which is on the card), and the Pi heartbeat. `Lens` says what it returns.
- The camera program sends `PiHealth` on its own timer as a heartbeat, so temperature and disk keep updating when pictures stop (`raspberry_pi/camera/workers.py`, a route in `desktalk/web/routers/pi.py`).
- `/api/health` shows each measure; the dev page's health tab reads the same numbers.
- Starting numbers (to tune): SD card warning under 2000 MB free, serious under 500 MB; no new detections for 300 s while the camera is connected; temperature warning at 75 °C (as now), serious at 80 °C.

### D. Clock
- `clock_synced` with each saved object-state row (`desktalk/domain/object_states.py`, `desktalk/adapters/storage/object_states_db.py`, the fake and the contract suite), added the way `boot_id` was.
- A clock that is plainly wrong (before `clock.earliest_believable_date`, say the 1990 test) is treated like a clock that hasn't synced, and nothing is pruned or deleted by date while it is wrong (`desktalk/adapters/storage/event_log.py` prune).
- Intervals use the monotonic clock: `desktalk/runtime/scheduler.py`, `raspberry_pi/camera/cleaner.py`.
- `docs/CLOCK.md` (new): the RTC battery decision for the Pi 5.

### E. SD card space and backup
- `raspberry_pi/update.sh`: prune Docker's build cache and old images after a rebuild, keeping a set amount.
- A cap on the object-state database (keep N days) and on the data folder, without breaking the append-only rule for recent rows.
- `scripts/backup_pi.sh` (new, **on your Mac**): copies the Pi's data folder (the database through SQLite's backup) and own settings over `ssh pi` into a dated folder and keeps the last N; `scripts/install_weekly_backup.sh` sets up a weekly launchd job on the Mac.

### F. Runbook and hand tests
- `docs/RUNBOOK.md` (new): for camera and SD card failures, what the user sees or hears, what to check, what to do; restoring from a backup.
- `docs/PI_CHECKS.md`: steps for each hand test in row 11, and a `-m pi` check to run after each where one can be written (`tests/pi/test_real_pi.py`).

## Left for the user

- Merging PR #16.
- Anything that needs `sudo` on the Pi (installing the unit and the journald file is done by `install.sh`).
- The hand tests in row 11.
- Buying the RTC battery, if `docs/CLOCK.md` recommends it.
