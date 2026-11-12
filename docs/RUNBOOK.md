# When the Pi needs a person

The Pi starts everything again by itself after a power cut, a crash or a lost network. This page is for the times it
can't fix itself: the camera, the SD card, the clock and the heat. Each part says what you notice, what to check,
what to do, and when it's time to replace a part.

Every command says where it runs: **on the Pi** (over `ssh pi`, or with a keyboard and screen) or **on your Mac**
(from the project folder). The Pi's project folder is `~/desk-talk`, and the commands for the containers run in
`~/desk-talk/raspberry_pi`.

To try each of these on purpose and see that the Pi does the right thing, see "Failure hand tests" in
[PI_CHECKS.md](PI_CHECKS.md).

## First, for anything

1. **On your Mac**, save the Pi's logs into one file before you change anything, so they're there if you need to look
   back or ask for help:

   ```bash
   bash scripts/pi_logs.sh
   ```

   It reaches the Pi with `ssh pi` (`--host` for another name, `--hours` for more than the last 24) and saves one
   `.tar.gz` into `pi_logs/` in the project folder: each container's log, the list of boots, `docker compose ps`, the
   disk and the temperature. **On the Pi**, `bash ~/desk-talk/raspberry_pi/get_logs.sh` (or `get_logs.sh 6` for 6
   hours) does the same and leaves the file in `~/desk-talk/pi_logs/`. See "Where the logs are" in
   [raspberry_pi/README.md](../raspberry_pi/README.md).

2. **On the Pi**, see what's running:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose ps
   ```

   All three (`app`, `camera`, `detector`) should say `healthy`. `Restarting` means it starts, fails and starts again.

3. **On the Pi**, how the app sees each part:

   ```bash
   curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool | less
   ```

   Its `checks` part has each health check with a level (`ok`, `warning`, `serious`, or `unknown` when it can't
   tell): `camera` (pictures arriving), `detections` (seconds since a picture last went through object detection),
   `pi_temperature` (°C), `card` (MB free on the SD card) and `pi_heartbeat` (seconds since the camera program last
   checked in). Its `pi` part has what the camera said lately, the detector's state and why.

The logs live in the Pi's journal, on the SD card, which keeps at most 200 MB and nothing older than 30 days, and
survives a restart. The containers log there too, as `desktalk-app`, `desktalk-detector` and `desktalk-camera`. **On
the Pi**, `journalctl -b -1` shows the boot before this one: the lines just before a power cut or a crash, and
`journalctl -t desktalk-camera --since "2 hours ago"` the camera's lines, from containers a rebuild replaced too.

Desk Talk itself starts with the Pi: `desktalk.service` runs `docker compose up -d` once Docker is up, with or without
the network. **On the Pi**, `systemctl status desktalk` shows whether it ran ("active (exited)" means it started them),
and `journalctl -u desktalk -b` what it said.

## The camera isn't seen, or keeps restarting

**What you notice**

- Lens says: "I lost the pictures from the Raspberry Pi camera." The live view on the page stops.
- Or, after the Pi starts, there are no pictures at all, and 2 minutes on (`lens.wait_before_saying_sec.no_pictures`)
  Lens says: "I haven't had a single picture from the Raspberry Pi camera since I started."
- `docker compose ps` shows `camera` as `Restarting` or `unhealthy`.

**Check**

1. **On the Pi**, what the camera said last:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --tail 50 camera
   ```

   - "Camera problem: the Pi camera module could not start": the camera module isn't there for the program. Most often
     the ribbon cable.
   - "No picture from the camera ... it seems stuck": it was working, then stopped giving pictures. The camera
     program stopped itself, and Docker started it again. Once is fine. Many times an hour is not.

2. **On the Pi**, test the camera with the container stopped (only one program can use the camera at a time):

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose stop camera
   ```

   ```bash
   rpicam-hello --list-cameras
   ```

   It should list one camera. No camera listed means the Pi can't see it: the cable, or the camera module itself.
   Afterwards, start the container again:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose up -d
   ```

**Fix**

- **The ribbon cable. Switch the Pi off first** (**on the Pi**, `sudo shutdown -h now`, then wait for the green light
  to stop, then pull the plug). Never take the camera cable out or put it in with the power on: it can break the camera
  or the Pi. Open the connector's latch, push the cable all the way in, straight, and close the latch. Do both ends.
  A Pi 5 needs its own narrower cable; the one for older Pis doesn't fit it.
- Switch it on. Everything starts by itself; wait 3 minutes and check `docker compose ps` again.
- If `rpicam-hello` sees the camera but the container still says it can't start it, a kernel update may have changed
  the numbers of the camera's devices. **On the Pi**, `bash ~/desk-talk/raspberry_pi/update.sh` fills them in again.
- A USB webcam: unplug it and plug it into another USB port. That's safe with the power on.

**When to replace hardware**

- With the cable pushed in well at both ends, `rpicam-hello --list-cameras` still lists nothing: try another cable
  first (they're cheap and they crack at the folds), then another camera module.
- "It seems stuck" many times a day, with a good cable: the camera module or the cable is failing. Replace the cable,
  then the module.

## The camera sees nothing new (the detector is busy or off)

**What you notice**

- Lens says: "The Raspberry Pi isn't finding the objects right now." The live view still works.
- Or Lens says: "The camera is on, but no picture has been through object detection for 5 minutes": the camera is
  sending pictures, but the detector is so busy it never answers (or stuck). After 15 minutes it says it again, more
  firmly (`health_checks.no_detections_warning_sec` and `no_detections_serious_sec`).
- Objects you put on the desk don't show up under "On the desk".

**Check**

1. **On the Pi**, the camera's status line, every 30 seconds:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --tail 5 camera
   ```

   In `detector ok 46594, busy 1145, off 0`, `ok` should keep going up. Only `busy` going up means the detector is
   too slow for the pictures. Only `off` going up means it's not answering at all.

2. **On the Pi**, the detector's own log:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --tail 50 detector
   ```

3. The temperature (the Pi slows itself down when it's hot): see "The Pi is too hot" below.

**Fix**

- **Off**: **on the Pi**, `cd ~/desk-talk/raspberry_pi && docker compose up -d`. The camera goes back to it by
  itself once it's ready: after 3 failures in a row (`pi.detector.failures_before_pause`) it stops asking for a while,
  5 seconds at first and longer each time up to a minute, with a random part, and asks the detector's `/health` every
  10 seconds meanwhile. "The detector has no model yet" or `/health` saying `error`: see "The detector container is
  down" in [raspberry_pi/README.md](../raspberry_pi/README.md).
- **Busy all the time**: give it less to do. **On the Pi**, in `raspberry_pi/mine/settings.mine.yaml`, a smaller
  picture size (`pi.detector.image_size`) or fewer pictures a second (`pi.fps`), then
  `cd ~/desk-talk/raspberry_pi && docker compose restart app camera`.

**When to replace hardware**

Never for this alone. If it only happens when the Pi is hot, fix the cooling.

## The SD card is nearly full

**What you notice**

- Lens says "The SD card is getting full, only ... MB left" under 2000 MB free, and "The SD card is almost full"
  under 500 MB (`health_checks.card_warning_below_mb` and `card_serious_below_mb`). `/api/health` → `checks` →
  `card` has the number.
- Later, if nothing is done: saving fails, and the container logs say "No space left on device".

**Check**

**On the Pi**, how much is free, and what's using it:

```bash
df -h /
```

```bash
docker system df
```

```bash
du -sh ~/desk-talk/data/* | sort -h
```

```bash
journalctl --disk-usage
```

Docker's saved build steps (`Build Cache` in `docker system df`) are usually the biggest: about 13 GB.

**Fix**

1. **On your Mac**, make a backup first: `bash scripts/backup_pi.sh` (the app must be running on the Pi).
2. **On the Pi**, clear Docker's saved build steps and old images. Nothing of yours is in them; the next rebuild just
   takes longer (12 to 21 minutes):

   ```bash
   docker builder prune -f && docker image prune -f
   ```

   `update.sh` also cuts them down after each rebuild, to `INSTALL_KEEP_BUILD_STEPS_MB` in `raspberry_pi/.env`
   (20 GB), and clears them all if the card runs low. If it said they "weren't cut down", this Docker refused both ways
   of asking (`--max-used-space`, `--keep-storage`): `docker system df` shows how much they take.
3. The journal keeps itself under 200 MB, the event log keeps `event_log.keep_days` (30) days, and the object-state
   database `object_states.keep_days` (30) days. If the data folder is still big, look at what `du` showed before
   deleting anything, and ask first.
4. Something else on the Pi using the room (a big download, a file you copied there): delete it.

The pictures themselves are kept in the Pi's memory, not on the card, so they never fill it.

**When to replace hardware**

If the card is smaller than 32 GB, get a bigger one: the three images and the build steps alone take about 13 GB.
See "Replacing the SD card" below.

## The SD card is read-only or failing

**What you notice**

- Logs say "Read-only file system", or saving stops with nothing about a full card.
- The Pi won't start, or starts and then hangs, or files come back broken.
- `docker compose` commands fail in odd ways.

**Check**

**On the Pi**, is the card mounted read-only (`ro` at the start means yes)?

```bash
findmnt -no OPTIONS /
```

**On the Pi**, the kernel's messages about the card, from this boot and the one before:

```bash
journalctl -k -b --no-pager | grep -i -E 'mmc|ext4|i/o error|read-only'
```

```bash
journalctl -k -b -1 --no-pager | grep -i -E 'mmc|ext4|i/o error|read-only'
```

Signs of a failing card: `EXT4-fs error`, `Remounting filesystem read-only`, `Buffer I/O error on dev mmcblk0`,
`mmc0: ... error` or `timeout`. One after a power cut can happen; many, or the same ones again after a repair, mean the
card is wearing out.

**Fix**

1. **Back up now, while it still reads.** **On your Mac**: `bash scripts/backup_pi.sh`. It copies the database with
   SQLite's own backup inside the app container, so the app must still be running.
2. The Pi repairs small problems by itself at boot (`fsck.repair=yes` in its boot settings). **On the Pi**,
   `sudo reboot`, and check again.
3. Still read-only, or the Pi won't start: repair the card on another computer. A Mac can't repair the Pi's kind of
   disk, so this needs a Linux computer or another Pi, with a USB card reader:
   1. Switch the Pi off (`sudo shutdown -h now` **on the Pi**, if it still answers) and take the card out.
   2. Put it in the reader on the other computer. Find its second part (the big one): `lsblk` shows it, for example
      `sdb2`.
   3. If it was mounted by itself, unmount it: `sudo umount /dev/sdb2`.
   4. Check and repair it: `sudo e2fsck -f /dev/sdb2`. Answer `y` to the repairs it offers.
   5. Put the card back in the Pi and switch it on.

**When to replace hardware**

The errors come back after a repair, the card goes read-only again, or `e2fsck` finds many problems: replace the card
now, before it stops. Cards wear out with writing. Pick a good-quality card of 32 GB or more, marked A1 or A2, ideally
one sold for "high endurance".

## Replacing the SD card

You need a new card (32 GB or more), a card reader for your Mac, and the latest backup.

1. **On your Mac**, the latest backup, if the old card still reads: `bash scripts/backup_pi.sh`.
2. **On your Mac**, write Raspberry Pi OS to the new card with Raspberry Pi Imager: Raspberry Pi 5, Raspberry Pi OS
   (64-bit; Lite is enough). In its settings:
   - the same name as before (the `HostName` for `pi` in your Mac's `~/.ssh/config`, without `.local`),
   - the same user (`sam`) and your Wi-Fi,
   - SSH on, with your Mac's public key (`~/.ssh/id_ed25519.pub` or similar), not a password,
   - your time zone.
3. Put the card in the Pi and switch it on. Wait 3 minutes.
4. **On your Mac**, forget the old card's SSH key, then log in:

   ```bash
   ssh-keygen -R desktalk-pi.local && ssh pi uptime
   ```

   Use your Pi's name in place of `desktalk-pi` if it's another. Say `yes` to the new key.
5. **On the Pi**, install Desk Talk ([raspberry_pi/README.md](../raspberry_pi/README.md), "Install with one
   command"):

   ```bash
   sudo apt install -y git && git clone https://github.com/randomnoobi/desk-talk ~/desk-talk && bash ~/desk-talk/raspberry_pi/install.sh
   ```

   It installs Docker, switches on `desktalk.service` (start on boot) and keeps the journal on the card
   (`/etc/systemd/journald.conf.d/60-desktalk.conf`), fetches the model and starts the three containers. If it says
   the Pi needs a restart, **on the Pi**, `sudo reboot`.
6. Restore the backup: the next part.
7. **On your Mac**, check it: `DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test`.
8. **On your Mac**, the weekly backup keeps working with no change, as long as `ssh pi` reaches the new card (step 4
   took its new key).

## Restoring from a backup

A backup is a folder on your Mac, `~/DeskTalkBackups/<day>-<time>/` (like `2026-11-10-100000`), laid out like the
repo on the Pi: `data/` (the event log, the object states, habits, to-dos, desk rules, story pictures),
`raspberry_pi/.env` and `raspberry_pi/mine/` (your own settings). `bash scripts/backup_pi.sh` makes one and keeps the
newest 8; `bash scripts/install_weekly_backup.sh` runs it every Sunday at 10:00. The steps are the same as in
"Backups" in [raspberry_pi/README.md](../raspberry_pi/README.md).

The pairing key and the models aren't in it: `install.sh` makes a new key and fetches the model.

1. **On your Mac**, pick the backup, the newest unless you want an older one:

   ```bash
   ls ~/DeskTalkBackups
   ```

2. **On the Pi**, stop the containers and remove the database's side files, which belong to the database being
   replaced:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose stop
   rm -f ../data/object_states.sqlite-wal ../data/object_states.sqlite-shm
   ```

3. **On your Mac**, copy the backup back over (put the folder you picked in place of `2026-11-10-100000`):

   ```bash
   rsync -a ~/DeskTalkBackups/2026-11-10-100000/ pi:desk-talk/
   ```

4. **On the Pi**, start again:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose up -d
   ```

5. **On your Mac**, check: `DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test`. Open the page and
   see your to-dos and habits.

Objects on the desk start over after a restore onto a new card (the saved states are from another boot, and its clock
may not have synced yet), which is expected.

## The clock is wrong after a power cut

**What you notice**

- After a power cut with no internet, the times in the log and on the page are off, often by hours.
- Objects that were on the desk start over instead of picking up where they were.
- Lens says "The Raspberry Pi's clock is ... seconds behind the app's."

**Why**

The Pi 5 has a clock chip, but without its battery it forgets the time when the power goes. Until it reaches the
internet, it starts from about the time it last shut down. See [CLOCK.md](CLOCK.md) for the battery and which one
to buy.

While the clock hasn't been set from the internet, or is plainly wrong (before `clock.earliest_believable_date`,
2025-01-01), the app picks up only the object states this boot saved, and marks each object state it saves with
`clock_synced` false. A plainly wrong clock also deletes nothing by date (no old event-log days, no old object
states), and the app's log says "The clock says 1990-01-01, before clock.earliest_believable_date ...". Timed jobs go
by the time since boot, so a clock jump doesn't hold them up. Once the clock is right everything carries on; nothing
needs a restart.

**Check**

**On the Pi**:

```bash
timedatectl
```

`System clock synchronized: yes` and `NTP service: active` mean it's right.

**Fix**

- Get the Pi on the internet. It sets its clock within a minute.
- `NTP service: inactive`: **on the Pi**, `sudo timedatectl set-ntp true`.
- Still not synced after a few minutes online: **on the Pi**, `sudo systemctl restart systemd-timesyncd`, then
  `journalctl -u systemd-timesyncd -n 20` says why.

**When to replace hardware**

If power cuts are common where the Pi lives, or it often starts with no internet, fit the clock battery (the J5
connector), as [CLOCK.md](CLOCK.md) says.

## The Pi is too hot

**What you notice**

- Lens says "The Raspberry Pi is running hot (75°C), so it may slow down." at 75 °C, and "The Raspberry Pi is far too
  hot (80°C) and is slowing itself down to cool off." at 80 °C (`pi.hot_warning_c` and `pi.hot_serious_c`). The
  camera program sends the temperature every 30 seconds (`pi.heartbeat_every_sec`), even when no pictures go out.
- Or "The Raspberry Pi is slowing itself down because it's too hot."
- Detection gets slower: more `busy` in the camera's status line.

**Check**

**On the Pi** (not inside a container):

```bash
vcgencmd measure_temp
```

```bash
vcgencmd get_throttled
```

`throttled=0x0` means it never slowed down since it started. The fan's speed, from 0 (off) to 4 (full):

```bash
cat /sys/class/thermal/cooling_device0/cur_state
```

**Fix**

- Give it air: out of the sun, not in a closed box, the fan's holes clear.
- A cooler or a fan, if it has none.
- Less work: a smaller picture size (`pi.detector.image_size`) or fewer pictures a second (`pi.fps`) in
  `raspberry_pi/mine/settings.mine.yaml`, then `cd ~/desk-talk/raspberry_pi && docker compose restart app camera`.
- If Lens says the power supply is too weak, use the official Raspberry Pi 5 power supply (27 W).

**When to replace hardware**

The fan reads 4 but doesn't spin or make a sound: replace the fan or the cooler. Still above 80 °C with a working fan in
a cool room: check the cooler sits flat on the chip, and re-seat it.
