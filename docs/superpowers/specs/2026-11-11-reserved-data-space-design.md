# Desk Talk's own room on the SD card (design)

Date: 2026-11-11. Branch: `pi-reliability` (the setup the Pi runs: three containers, `raspberry_pi/install.sh`).

## Why

On 2026-11-11 the Pi's SD card filled up to 400 MB free. The cause was an 87 GB file, `~/desktalk-fill`, left behind
by the full-card hand test in `docs/PI_CHECKS.md` (its undo step was never run). Desk Talk's own cleanup was fine: it
only ever trims Desk Talk's own files, and its data folder was 39 MB. But anything else on the Pi can fill the card the
same way: a stray download, another program's runaway log, a forgotten test file.

The app writes as the Pi's normal user (`PI_USER_ID`, 1000: user `sam`). That is the same user that made the test file,
so the 5% of an ext4 card that only root may use doesn't protect Desk Talk.

The person's priority, in their words: **stay operational is the top priority**, and **it must keep saving history**.

## Goal

Desk Talk gets a fixed amount of space on the card, set aside for it alone. Files written anywhere else on the Pi can
fill the rest of the card, but they can't take that space. While the card outside it is full, Desk Talk keeps watching,
chatting, speaking and saving its history (the event log, the object states, habits, to-dos, story pictures, its own
log files), and Lens tells the person that the rest of the card is full so they can clean up.

## How (decided)

- **A fixed-size disk image file on the card, holding its own ext4 filesystem, mounted at the data folder**
  (`DESKTALK_DATA`, `~/desk-talk/data` today). The space is set aside when the file is made, so nothing outside it can
  use it up, root included (unless root writes inside it on purpose).
- **Not a share of the card, but a fixed size, set in the settings.** The person first asked for 80% of the card. That
  doesn't fit: 80% of 117 GB is 94 GB, leaving 23 GB, and the system, Docker's images (7 GB) and its build cache
  (13 GB) already use 25 GB. Desk Talk's history is small, because it's already capped (30 days of events and object
  states, a set number of story pictures, 5 log files of 5 MB). The person agreed to a fixed size in the settings
  instead.
- **Not repartitioning the card.** Changing partitions on a running Pi can lose everything if it goes wrong. The image
  file needs no change to the card's partitions.

## Requirements

1. **The space is really set aside.** The image file's blocks must all be allocated when it is made (`fallocate`, not a
   sparse file, whose blocks would only be taken as they're written and could be taken by others first). Check this
   after making it (the space the file uses on the card equals its size).
2. **The size is a setting.** A new name in `raspberry_pi/.env.example` (install.sh's numbers live there, next to
   `CAMERA_PHOTOS_SIZE` and `JOURNAL_MAX_USE`), with a plain-language comment. Default: **8 GB** (my call: about 200
   times what the data folder holds after 10 days, and about 7% of this card). install.sh refuses, with a plain
   message, if making it would leave less than a set amount free on the card (another new `.env` name; it must at least
   cover a build, see `check_disk`).
3. **Growing it later.** If the person raises the size in `.env` and runs install.sh again, the file and its filesystem
   grow to the new size (ext4 can grow while mounted). Shrinking is not supported; install.sh says so and leaves it.
4. **It is mounted before the containers start, every boot.** Through systemd (a mount unit, or an `/etc/fstab` line
   with a loop mount), ordered before Docker (`Before=docker.service`). The containers wait for it but don't require
   it: if it can't be mounted they still start, in the save-nothing mode of requirement 5 (the person's choice).
   install.sh puts the unit in place the same way it does the others (`put_system_file`, `set_up_start_on_boot`).
5. **No silent writing to the card if the mount is missing.** If the image isn't mounted, the data folder is just an
   empty folder on the card: the app would start a second, empty history there and fill the card, and the person's real
   history would look lost. Prevent it: the empty mount point belongs to root and can't be written by the Pi's user.
   When the data folder isn't the mounted image, the app **still starts, saves nothing, and says so** (the person's
   choice): it keeps watching, chatting and speaking; it writes nothing to the data folder; Lens says plainly that
   history isn't being saved and what to do, and the page and `/api/health` show it too. The start script
   (`raspberry_pi/app/start.sh`) decides this at start and tells the app (a flag), so the app never finds out half-way.
   Docker's own restart rule (`restart: unless-stopped`) also starts the containers at boot, not only
   `desktalk.service`, so this check can't rely on systemd's ordering alone. At boot, systemd runs only the safe
   automatic filesystem repair on the image (the `-p` repair, as for any fstab line with a pass number); anything it
   can't fix safely is left for the person, and the app runs in the save-nothing mode until then.
6. **Existing history moves in, with nothing lost.** The first install.sh (or update.sh) run with this feature stops the
   app, copies the current data folder into the new image (keeping owners, times, and the SQLite files with their
   `-wal` and `-shm` files), checks the copy, then swaps it in. The old folder is kept, renamed, until the person
   deletes it, and install.sh's closing notes say where it is and how to delete it. If anything fails part-way, the old
   folder is left exactly as it was and the app starts on it as before.
7. **Owned by the Pi's user,** as the data folder is today, so the app writes as before and the folder can still be
   backed up or copied with scp.
8. **Two free-space checks instead of one.** Today `desktalk/core/health/checks.py` measures the disk the data folder
   is on (`desktalk/adapters/storage/disk_space.py`). With the image, that's Desk Talk's own room, not the card.
   - **Desk Talk's room** (the data folder): the existing check, with its own warning and serious levels.
   - **The rest of the card**: a new check, with its own levels and wording in `config/prompts.yaml`. The app sees the
     card through its container's own `/` (Docker keeps it on the card). When the card is full, Lens says that the rest
     of the card is full, that Desk Talk is still saving, and what can't work until there's room (updates and builds,
     and the Pi's own logs in the journal).
   Both show on `/api/health` under `checks`, and both levels go in `config/settings.yaml` under `health_checks` with a
   typed field in `desktalk/config/app.py`.
9. **Dry run.** `install.sh --dry-run` says what it would do (make a file of this size, move this much data) and changes
   nothing, like its other steps.
10. **Docs.** `raspberry_pi/README.md` explains the room, how to change its size, and how to read both checks. The
    full-card hand test in `docs/PI_CHECKS.md` changes: filling the card should now give the "rest of the card" warning,
    and history must keep being saved (the event log keeps growing, object states keep being written). Its undo step
    gets a check that `~/desktalk-fill` is gone.

## Not goals

- Protecting Desk Talk from root. Root can write anywhere, including inside the image.
- Keeping other programs that run as the Pi's own user out of the data folder itself. They can still write there
  (the app runs as that user). A separate user for the app would stop that, but it changes who can back up the folder
  and how the sound card and camera are reached. Left out on purpose; see "Questions".
- Deleting or moving files Desk Talk doesn't own to make room. It warns; the person decides.
- The Docker volumes (`models`, `pairing`) and the Pi's journal. They stay on the card. The models are already
  downloaded, the pairing key is tiny, and the journal has its own cap (`JOURNAL_MAX_USE`).
- A setup that runs on the person's Mac (`desk-talk`'s own `fix-double-names` work). This is for the Pi only.

## Anti-goals (would make it a failure even if it works)

- Any way for history to be lost: during the move, when the mount is missing, or when the image fills.
- The app starting on an empty data folder without saying so.
- install.sh getting slower on every update (the move and the size check must be quick to skip once done).

## Constraints

- AGENTS.md rules: no hardcoded numbers (Pi install numbers in `raspberry_pi/.env.example`, app numbers in
  `config/settings.yaml`), wording in `config/prompts.yaml`, tests first, ruff, pyright and vulture clean, coverage only
  up.
- install.sh's style: safe to run again, never touches the data, asks for sudo only where needed and says what to run
  by hand when sudo doesn't work (`later`).
- Nothing can be run on the Pi from here: each Pi step gets **On the Pi** commands for the person to run.

## Left to the builder

- systemd mount unit or fstab line; how the start script recognises the mounted image (a marker file inside the image,
  or comparing the device of the data folder with its parent).
- The exact names of the new `.env` and `health_checks` settings, and the wording, matching what's there.
- Where the image file lives (my suggestion: next to the data folder, e.g. `~/desk-talk/data.img`, outside git).

## Calls made after the builder check (mine, to check)

- **A card too small for the default room** (a 32 GB card, which `.env.example` says is enough
  (`INSTALL_MIN_DISK_MB=14000`), has only a few GB left after the system, the images and the build cache): install.sh
  makes the biggest room that still leaves the rebuild reserve free (`INSTALL_MIN_DISK_BUILT_MB`, since the room is made
  after the first build), down to a smallest room (a new `.env` name, default 1 GB: still about 25 times what 10 days of
  history take). Below that it stops with a plain message saying how much to free. It says the size it made.
- **A plain `docker compose up` without install.sh still works**, as `start.sh`, `raspberry_pi/README.md` and the tests
  (`tests/docker/test_pi_stack.py`, `tests/unit/test_pi_app_start_script.py`) expect. The check in requirement 5 is
  switched on by an `.env` name that install.sh writes (it is off in `compose.yaml`'s default and in `.env.example`'s
  comment says install.sh sets it). When it's on, the start script looks for a marker file that install.sh writes inside
  the image when it makes it. Inside the container the data folder is always a bind mount, so comparing devices can't
  tell. install.sh writes the name on every run (`write_env` / `add_new_names`), so a `.env` that lost it gets it back.
- **A data folder already off the SD card** (`DESKTALK_DATA` pointing at a USB drive): install.sh leaves it alone and
  makes no room, since it isn't on the card. It says so. The "rest of the card" check still measures the card.

## Answered by the person

- If the room can't be mounted at boot (its file is missing, or a power cut damaged its filesystem beyond what a safe
  automatic repair fixes): **run, warn, save nothing** (requirement 5). Staying up wins; history is never split across
  two places.
