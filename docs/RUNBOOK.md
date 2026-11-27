# Runbook: when the Pi needs a person

The Pi starts everything again by itself after a power cut, a crash or a lost network. This page is for the times it
can't fix itself (the camera, the SD card, the clock, the heat), and for checking on purpose that it does the right
thing ([Checking on the real Pi](#checking-on-the-real-pi)).

Each problem says what you notice, what to check, what to do, and when to replace a part. Every command says where it
runs: **on the Pi** (over `ssh pi`, or with a keyboard and screen) or **on your Mac** (in the project folder). The
Pi's project folder is `~/desk-talk`; the commands for the containers run in `~/desk-talk/raspberry_pi`.

## First, for anything

1. **On your Mac**, save the Pi's logs into one file before you change anything:

   ```bash
   bash scripts/pi_logs.sh
   ```

   It reaches the Pi with `ssh pi` (`--host` for another name, `--hours` for more than the last 24) and saves one
   `.tar.gz` into `pi_logs/`: each container's log, the list of boots, `docker compose ps`, the disk and the
   temperature. **On the Pi**, `bash ~/desk-talk/raspberry_pi/get_logs.sh` (or `get_logs.sh 6` for 6 hours) does the
   same and leaves the file in `~/desk-talk/pi_logs/`.

2. **On the Pi**, see what's running:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose ps
   ```

   All three (`app`, `camera`, `detector`) should say `healthy`. `Restarting` means it starts, fails and starts again.

3. **On the Pi**, see how the app sees each part:

   ```bash
   curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool | less
   ```

   Its `checks` part gives each health check a level (`ok`, `warning`, `serious`, or `unknown` when it can't tell):
   `camera` (pictures arriving), `detections` (seconds since a picture last went through object detection),
   `pi_temperature` (°C), `card` (MB free in Desk Talk's own space), `card_rest` (MB free on the rest of the SD
   card), `saving` (whether history is being saved) and `pi_heartbeat` (seconds since the camera program last
   checked in). Its `pi` part has what the camera said lately, and the detector's state and why.

The logs live in the Pi's journal, on the SD card. It keeps at most 200 MB and nothing older than 30 days, and
survives a restart. The containers log there as `desktalk-app`, `desktalk-detector` and `desktalk-camera`. **On the
Pi**, `journalctl -b -1` shows the boot before this one (the lines just before a power cut or a crash), and
`journalctl -t desktalk-camera --since "2 hours ago"` the camera's lines, including from containers a rebuild
replaced.

Desk Talk starts with the Pi: `desktalk.service` runs `docker compose up -d` once Docker is up, with or without the
network. **On the Pi**, `systemctl status desktalk` shows whether it ran ("active (exited)" means it started them), and
`journalctl -u desktalk -b` what it said.

## The camera isn't seen, or keeps restarting

**What you notice**

- Lens says: "I lost the pictures from the Raspberry Pi camera." The live view stops.
- Or, after the Pi starts, there are no pictures at all, and 2 minutes on (`lens.wait_before_saying_sec.no_pictures`)
  Lens says: "I haven't had a single picture from the Raspberry Pi camera since I started."
- `docker compose ps` shows `camera` as `Restarting` or `unhealthy`.

**Check**

1. **On the Pi**, what the camera said last:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --tail 50 camera
   ```

   - "Camera problem: the Pi camera module could not start": the program can't reach the camera module. Most often
     the ribbon cable.
   - "No picture from the camera ... it seems stuck": it was working, then stopped giving pictures, so the camera
     program stopped itself and Docker started it again. Once is fine, but not many times an hour.

2. **On the Pi**, test the camera with the container stopped (only one program can use the camera at a time):

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose stop camera
   ```

   ```bash
   rpicam-hello --list-cameras
   ```

   It should list one camera. None listed means the Pi can't see it: the cable, or the camera module itself. Then
   start the container again:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose up -d
   ```

**Fix**

- The ribbon cable. **Switch the Pi off first** (**on the Pi**, `sudo shutdown -h now`, wait for the green light to
  stop, then pull the plug). Never take the camera cable out or put it in with the power on: it can break the camera
  or the Pi. Open the connector's latch, push the cable all the way in, straight, and close the latch. Do both ends. A
  Pi 5 needs its own narrower cable; the one for older Pis doesn't fit.
- Switch it on. Everything starts by itself; wait 3 minutes and check `docker compose ps` again.
- If `rpicam-hello` sees the camera but the container still can't start it, a kernel update may have changed the
  numbers of the camera's devices. **On the Pi**, `bash ~/desk-talk/raspberry_pi/update.sh` fills them in again.
- A USB webcam: unplug it and plug it into another USB port. That's safe with the power on.

**When to replace hardware**

- With the cable pushed in well at both ends, `rpicam-hello --list-cameras` still lists nothing: try another cable
  first (they're cheap and crack at the folds), then another camera module.
- "It seems stuck" many times a day with a good cable: the module or the cable is failing. Replace the cable, then the
  module.

## The camera sees nothing new (the detector is busy or off)

**What you notice**

- Lens says: "The Raspberry Pi isn't finding the objects right now." The live view still works.
- Or Lens says: "The camera is on, but no picture has been through object detection for 5 minutes": pictures are
  coming, but the detector is too busy to answer, or stuck. After 15 minutes it says so again, more firmly
  (`health_checks.no_detections_warning_sec` and `no_detections_serious_sec`).
- Objects you put on the desk don't show up under "On the desk".

**Check**

1. **On the Pi**, the camera's status line, written every 30 seconds:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --tail 5 camera
   ```

   In `detector ok 46594, busy 1145, off 0`, `ok` should keep going up. Only `busy` going up means the detector is too
   slow for the pictures. Only `off` going up means it isn't answering at all.

2. **On the Pi**, the detector's own log:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --tail 50 detector
   ```

3. The temperature, since the Pi slows itself down when it's hot: see [The Pi is too hot](#the-pi-is-too-hot).

**Fix**

- If it's off: **on the Pi**, `cd ~/desk-talk/raspberry_pi && docker compose up -d`. The camera goes back to the detector
  by itself once it's ready. After 3 failures in a row (`pi.detector.failures_before_pause`) it stops asking for a
  while (5 seconds at first, longer each time up to a minute) and checks the detector's `/health` every 10 seconds
  meanwhile. For "The detector has no model yet", or `/health` saying `error`, see "The detector container is down"
  in [raspberry_pi/README.md](../raspberry_pi/README.md).
- If it's busy all the time, give it less to do. **On the Pi**, in `raspberry_pi/mine/settings.mine.yaml`, set a smaller
  picture size (`pi.detector.image_size`) or fewer pictures a second (`pi.fps`), then
  `cd ~/desk-talk/raspberry_pi && docker compose restart app camera`.

**When to replace hardware**

Never for this alone. If it only happens when the Pi is hot, fix the cooling.

## The SD card is getting full

Desk Talk saves everything into its own space on the card (`~/desk-talk/data.img`, mounted as `data/`), which has a
fixed size, so the rest of the card filling up doesn't stop it saving. See "Desk Talk's own space on the SD card" in
[raspberry_pi/README.md](../raspberry_pi/README.md).

**What you notice**

- Lens says the rest of the SD card is getting full, with how many MB are left, under 2000 MB free, and that it's
  almost full under 500 MB (`health_checks.card_rest_warning_below_mb` and `card_rest_serious_below_mb`). It adds that
  Desk Talk is still saving: updates and the Pi's logs are what need the room.
- Or Lens says Desk Talk's own space is getting full: under 300 MB free in it, and serious under 100 MB
  (`health_checks.room_warning_below_mb` and `room_serious_below_mb`).
- In `/api/health`, `checks` has the numbers in `card_rest` and `card`.

**Check**

**On the Pi**, how much is free, and what's using it:

```bash
df -h / ~/desk-talk/data
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

On the rest of the card, Docker's saved build steps (`Build Cache` in `docker system df`) are usually the biggest:
about 13 GB.

**Fix**

1. **On your Mac**, make a backup first: `bash scripts/backup_pi.sh` (the app must be running on the Pi).
2. For the rest of the card, **on the Pi**, clear Docker's saved build steps and old images. Nothing of yours is in
   them; the next rebuild just takes longer (12 to 21 minutes):

   ```bash
   docker builder prune -f && docker image prune -f
   ```

   `update.sh` also cuts them down after each rebuild, to `INSTALL_KEEP_BUILD_STEPS_MB` in `raspberry_pi/.env`
   (20 GB), and clears them all if the card runs low. If it said they "weren't cut down", this Docker refused both
   ways of asking: `docker system df` shows how much they take. Anything else of yours using the room (a big
   download, a file you copied there) can go too.
3. In Desk Talk's own space, the event log keeps `event_log.keep_days` (30) days and the object-state database
   `object_states.keep_days` (30) days, so it shouldn't fill. If it does, look at what `du` showed before deleting
   anything, or make the space bigger: raise `DATA_ROOM_SIZE_MB` in `raspberry_pi/.env` and run `install.sh` again.
   Never delete `data.img` itself: it holds the history.

The camera's pictures are kept in the Pi's memory, not on the card, so they never fill it.

**When to replace hardware**

If the card is smaller than 32 GB, get a bigger one: the three images and the build steps alone take about 13 GB. See
[Replacing the SD card](#replacing-the-sd-card).

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

1. Back up now, while it still reads. **On your Mac**: `bash scripts/backup_pi.sh`. It copies the database with
   SQLite's own backup inside the app container, so the app must still be running.
2. The Pi repairs small problems by itself at boot (`fsck.repair=yes` in its boot settings). **On the Pi**,
   `sudo reboot`, and check again.
3. Still read-only, or the Pi won't start: repair the card on another computer. A Mac can't repair the Pi's kind of
   disk, so this needs a Linux computer or another Pi, with a USB card reader:
   1. Switch the Pi off (`sudo shutdown -h now` **on the Pi**, if it still answers) and take the card out.
   2. Put it in the reader on the other computer. `lsblk` shows its second part (the big one), for example `sdb2`.
   3. If it was mounted by itself, unmount it: `sudo umount /dev/sdb2`.
   4. Check and repair it: `sudo e2fsck -f /dev/sdb2`. Answer `y` to the repairs it offers.
   5. Put the card back in the Pi and switch it on.

**When to replace hardware**

The errors come back after a repair, the card goes read-only again, or `e2fsck` finds many problems: replace the card
now, before it stops. Cards wear out with writing. Pick a good card of 32 GB or more, marked A1 or A2, ideally one sold
for "high endurance".

## Replacing the SD card

You need a new card (32 GB or more), a card reader for your Mac, and the latest backup.

1. **On your Mac**, take a last backup if the old card still reads: `bash scripts/backup_pi.sh`.
2. **On your Mac**, write Raspberry Pi OS to the new card with Raspberry Pi Imager: Raspberry Pi 5, Raspberry Pi OS
   (64-bit; Lite is enough). In its settings:
   - the same name as before (the `HostName` for `pi` in your Mac's `~/.ssh/config`, without `.local`),
   - the same user and your Wi-Fi,
   - SSH on, with your Mac's public key (`~/.ssh/id_ed25519.pub` or similar), not a password,
   - your time zone.
3. Put the card in the Pi and switch it on. Wait 3 minutes.
4. **On your Mac**, forget the old card's SSH key, then log in (use your Pi's name in place of `desktalk-pi`, and say
   `yes` to the new key):

   ```bash
   ssh-keygen -R desktalk-pi.local && ssh pi uptime
   ```

5. **On the Pi**, install Desk Talk:

   ```bash
   sudo apt install -y git && git clone https://github.com/randomnoobi/desk-talk ~/desk-talk && bash ~/desk-talk/raspberry_pi/install.sh
   ```

   It installs Docker, starts Desk Talk on boot, keeps the journal on the card, makes Desk Talk's own space, fetches
   the model and starts the three containers. If it says the Pi needs a restart, **on the Pi**, `sudo reboot`.
6. Restore the backup, as in the next part.
7. **On your Mac**, check it: `DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test`.

The weekly backup keeps working with no change, as long as `ssh pi` reaches the new card.

## Restoring from a backup

A backup is a folder on your Mac, `~/DeskTalkBackups/<day>-<time>/` (like `2026-11-10-100000`), laid out like the
project folder on the Pi: `data/` (the event log, the object states, habits, to-dos, desk rules, story pictures),
`raspberry_pi/.env` and `raspberry_pi/mine/` (your own settings). `bash scripts/backup_pi.sh` makes one and keeps the
newest 8; `bash scripts/install_weekly_backup.sh` runs it every Sunday at 10:00. The pairing key and the models aren't
in it: `install.sh` makes a new key and fetches the model.

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

3. **On your Mac**, copy the backup back over (with the folder you picked in place of `2026-11-10-100000`):

   ```bash
   rsync -a ~/DeskTalkBackups/2026-11-10-100000/ pi:desk-talk/
   ```

4. **On the Pi**, start again:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose up -d
   ```

5. **On your Mac**, check: `DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test`. Open the page and
   see your to-dos and habits.

Objects on the desk are expected to start over after a restore onto a new card: the saved states are from another
boot, and its clock may not have synced yet.

## The clock is wrong after a power cut

**What you notice**

- After a power cut with no internet, the times in the log and on the page are off, often by hours.
- Objects that were on the desk start over instead of picking up where they were.
- Lens says "The Raspberry Pi's clock is ... seconds behind the app's."

**Why**

The Pi 5 has a clock chip, but without a battery it forgets the time when the power goes. Until it reaches the
internet, it starts from about the time it last saved, which can be hours or days behind.

Desk Talk stays safe meanwhile. While the clock hasn't been set from the internet, or is plainly wrong (before
`clock.earliest_believable_date`, 2025-01-01), the app picks up only the object states this boot saved, and marks each
state it saves with `clock_synced` false. A plainly wrong clock, or one that jumped more than
`clock.largest_jump_ahead_min` ahead without the time server setting it, deletes nothing by date (no old event-log
days, no old object states), and the app's log says so once. Timed jobs go by the time since boot, so a clock jump
doesn't hold them up or rush them. Once the clock is right everything carries on; nothing needs a restart.

What a wrong clock still does: events go under the day the clock says (a 1990 clock writes a `1990-01-01` day, which
the next clean-up deletes once the clock is right), and the habit day can roll over early or late.

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

**When to replace hardware: the clock battery**

If power cuts are common where the Pi lives, or it often starts with no internet, fit the official Raspberry Pi RTC
Battery (a few dollars, a rechargeable coin cell with the right plug). With it, the clock is right straight after a
power cut, so saved object states come back and events go under the right day. All **on the Pi**:

1. Shut the Pi down, plug the battery into the connector marked BAT (J5), and stick it down.
2. Start the Pi. `timedatectl` shows `RTC time` next to the local time. Linux copies the synced time into the clock
   chip by itself; to do it right away, `sudo hwclock --systohc`.
3. The Pi doesn't charge the battery unless told to. Add this line to `/boot/firmware/config.txt` and reboot:

   ```
   dtparam=rtc_bbat_vchg=3000000
   ```

   Only for a rechargeable cell like the official one. Never charge an ordinary lithium coin cell.
4. To check the battery later: `cat /sys/devices/platform/soc/soc:rpi_rtc/rtc/rtc0/battery_voltage`.

To check it worked, pull the plug with the Wi-Fi off, wait a few minutes, start the Pi and run `timedatectl` before it
reaches the internet. The time should be right.

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
- Add a cooler or a fan, if it has none.
- Give it less work: a smaller picture size (`pi.detector.image_size`) or fewer pictures a second (`pi.fps`) in
  `raspberry_pi/mine/settings.mine.yaml`, then `cd ~/desk-talk/raspberry_pi && docker compose restart app camera`.
- If Lens says the power supply is too weak, use the official Raspberry Pi 5 power supply (27 W).

**When to replace hardware**

The fan reads 4 but doesn't spin or make a sound: replace the fan or the cooler. Still above 80 °C with a working fan
in a cool room: check the cooler sits flat on the chip, and re-seat it.

## Checking on the real Pi

`uv run pytest -m pi` runs against the real Pi over SSH. Most of it runs alone. The rest needs a person, so those tests
skip and name the part of this page to do. Each part below has the steps and what to run afterwards.

### The tests that run alone

They stop and start containers, and put everything back afterwards, even if a test fails. They take about 15 minutes.
**On your Mac**:

```bash
DESKTALK_PI=pi uv run pytest -m pi -v -rs
```

- `DESKTALK_PI` is what you'd type after `ssh`. The SSH login must use a key: the tests never ask for a password.
- If the Pi's compose file isn't in `~/desk-talk/raspberry_pi`, add `DESKTALK_PI_DIR='~/your-folder/raspberry_pi'`.
  Keep the quotes, so `~` means the Pi's home and not your Mac's.
- A skip isn't a pass. It means nobody has checked that part yet.

Two tests stand in for things only a person can do, and replace neither:

- `test_pictures_reach_the_app_and_the_detector_answers_for_each` shows pictures flowing and the detector answering,
  not that an object gets noticed. [A cup on the desk](#a-cup-on-the-desk) does that.
- `test_all_three_killed_come_back_readable` kills all three containers, but the Pi keeps running, so it isn't a power
  cut. [A power cut](#a-power-cut) is.

`test_disk_stays_under_limit` cuts `pi.delete_every_min` to 1 minute with a throwaway copy of the settings, and puts it
back at the end. It doesn't fill the disk, so deleting early at `pi.min_free_mb` is only covered by unit tests.

### A cup on the desk

Shows a real object turns into an `object_state` event with only the Pi running.

1. Point the camera at the desk, with the cup out of view.
2. **On your Mac**, start the test:

   ```bash
   DESKTALK_PI=pi DESKTALK_PI_OBJECT_ON_DESK=1 uv run pytest -m pi -v -rs -k test_works_with_nothing_else_on
   ```

3. Put the cup in view within 3 minutes, and leave it there.

It passes when the event shows up. A cup put down in the 5 minutes before the test also counts.

### A power cut

Shows the Pi starts everything by itself, and that pulling the plug leaves the event log and SQLite readable.

1. Put a cup on the desk and wait 2 minutes, so its state gets saved.
2. Pull the Pi's power plug. Don't shut it down first.
3. Plug it back in within about 3 minutes. Touch nothing on the Pi, and wait 3 minutes.
4. **On your Mac**, within 30 minutes of plugging it in:

   ```bash
   DESKTALK_PI=pi DESKTALK_PI_JUST_POWER_CUT=1 uv run pytest -m pi -v -rs -k test_starts_after_power_cut
   ```

It checks the Pi has only just booted, all three containers started by themselves and are healthy, pictures are
coming in, the event log and SQLite can be read, and the app picked up the right saved states: none while the clock
hadn't synced (see [The clock is wrong after a power cut](#the-clock-is-wrong-after-a-power-cut)). It also checks the
power really was cut: the app's log has no `Shutting down` line, so a `sudo reboot`, or stopping the containers first,
fails it on purpose.

The kernel can number the camera's devices differently at any boot. `desktalk.service` writes this boot's numbers into
`.env` before it starts the containers. `tests/pi/test_real_pi_start_on_boot.py` checks that, the service and the
journal settings on their own, at any time:

```bash
DESKTALK_PI=pi uv run pytest -m pi -v -rs tests/pi/test_real_pi_start_on_boot.py
```

### An app restart before the clock has synced

Shows that restarting only the app (as the watchdog or the memory limit does) keeps what this boot saved, on a Pi that
hasn't reached the internet since it booted. The Pi must be on the Wi-Fi, so the test can reach it, but must not have
set its clock since it booted. Either:

- With a router, unplug the router's internet cable (keep the router on), then pull the Pi's plug and put it back.
  Plug the internet cable back in only after the test.
- Without one, **on the Pi**, `sudo systemctl stop systemd-timesyncd && sudo rm -f /run/systemd/timesync/synchronized`.
  This only pretends: the clock is still right. Afterwards, **on the Pi**, `sudo systemctl start systemd-timesyncd`.

Then:

1. **On your Mac**, check the clock hasn't synced: `ssh pi ls /run/systemd/timesync/` must not list `synchronized`.
2. Put a cup on the desk and wait 2 minutes.
3. **On your Mac**:

   ```bash
   DESKTALK_PI=pi uv run pytest -m pi -v -rs -k test_app_restart_before_the_clock_has_synced
   ```

It restarts the app, then checks the app said its clock hadn't synced and still picked up what this boot saved in the
last `object_states.restore_max_age_sec` (the cup). It skips if the clock has synced, or if nothing was saved.

### No Wi-Fi at boot

Shows everything starts and works with no network at all.

1. Switch off your Wi-Fi router.
2. Pull the Pi's plug and put it back (or shut it down first: only the start matters here).
3. Wait 3 minutes. Switch the router back on, and wait until **on your Mac** `ssh pi uptime` answers.
4. **On your Mac**, within 30 minutes of the boot:

   ```bash
   DESKTALK_PI=pi DESKTALK_PI_NO_WIFI_AT_BOOT=1 uv run pytest -m pi -v -rs -k test_no_wifi_at_boot
   ```

It checks all three started by themselves and the data can be read. The proof the Wi-Fi was off is the journal:
NetworkManager's first `device (wlan0): Activation: successful` must come after each container's start. **On the
Pi**, this shows that line (your user must be in the `adm` group, as the first user is):

```bash
journalctl -b -u NetworkManager -o short-monotonic --no-pager | grep -m1 "device (wlan0): Activation: successful"
```

### No network at all

Shows that with no Wi-Fi and no AI the app keeps recording, and the speaker still talks. Nothing can reach the Pi
meanwhile, so a script on the Pi takes notes in `data/pi_checks/no_network.json`, and a test reads them once the Wi-Fi
is back.

1. To check speech as well, plug in the USB speaker first.
2. **On your Mac**, start the script. It waits up to 15 minutes for the network to go:

   ```bash
   ssh pi 'cd ~/desk-talk || exit 1; nohup python3 tests/pi/on_the_pi.py no-network > ~/no_network_check.log 2>&1 < /dev/null & sleep 3; cat ~/no_network_check.log; grep -q Started ~/no_network_check.log'
   ```

   It should print `Started: waiting for the network to go.` If it prints anything else, stop here: it isn't
   running.
3. Switch off your Wi-Fi router. SSH and the page stop working, as expected. With no router to switch off, **on the
   Pi** with a keyboard and screen (the Wi-Fi comes back by itself after 10 minutes):

   ```bash
   sudo sh -c 'nmcli radio wifi off; sleep 600; nmcli radio wifi on'
   ```

4. After a minute or two, put your hand over the camera's lens for 10 seconds, so Lens has something to say.
5. Wait 7 minutes from when the Wi-Fi went off. The script restarts the app once in that time, on purpose.
6. Switch the router back on. If the Wi-Fi came back too soon, the run says so and you do it again.
7. **On your Mac**, once `ssh pi uptime` answers:

   ```bash
   DESKTALK_PI=pi uv run pytest -m pi -v -rs -k no_network
   ```

The notes count for 24 hours. The speech test skips if no speaker was in or nothing was said.

### Listen to the speaker

1. Plug the USB speaker in while the app is running. Don't restart anything.
2. Put your hand over the camera's lens for 10 seconds.
3. Listen: do you hear Lens within a few seconds, and can you understand the words?
4. **On the Pi**, see that the app noticed:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --since 5m app | grep "out loud"
   ```

   You should see `Said a message out loud (Lens)`. `No sound card with "usb" in its name` means it didn't find the
   speaker; `Could not say a message out loud` says why it failed.

A webcam with a microphone can be picked ahead of the speaker, since its name has `usb` in it too. Then set
`notifications.sound_card` in `raspberry_pi/mine/settings.mine.yaml` to a word only the speaker's name has: **on the
Pi**, `cat /proc/asound/cards` lists them.

### A notification on your phone

Set up the topic first ([NOTIFICATIONS.md](NOTIFICATIONS.md#phone-notifications)).

1. Put your hand over the camera's lens for 10 seconds.
2. Your phone should show Lens's warning within a few seconds.
3. **On the Pi**, see that the app sent it:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --since 5m app | grep "your phone"
   ```

   You should see `Sent a notification to your phone (Lens)`. `Could not send a notification to your phone` says why
   it failed.

### Close the laptop while chat is on

Only for a Pi with `llm.ai_url` pointing at an AI on your laptop and `llm.ai_on_my_computer: true`. Shows warnings
don't wait for an AI that has gone away.

1. Send a chat message from the page and see the AI answer.
2. Close the laptop, and wait until it's asleep.
3. Put your hand over the camera's lens for 10 seconds. The warning should come in its ready-made words within a few
   seconds, on the speaker and on the page.
4. Send a chat message. It may take up to `llm.ollama_timeout_sec` (30 seconds on the Pi) to say the AI can't be
   reached, but the page must keep working.
5. Open the laptop. Chat should work again with nothing restarted.

### Open the page from a real phone

1. Have the phone on the same Wi-Fi as the Pi, not on mobile data.
2. Open `http://desktalk.local:5000`. If that name doesn't work, use the Pi's address (**on the Pi**, `hostname -I`).
3. See the live picture move. Put a cup in view and see it listed under "On the desk".
4. Add a to-do and press a habit button. Reload the page: both are still there.
5. Turn the phone sideways: nothing should get cut off.

### Failure hand tests

These are six things that go wrong in real life, done on purpose: a power cut, the network off, a pulled camera cable, a full SD
card, the clock set back to 1990, and Desk Talk's own space missing. Each shows the Pi gets through by itself, or says
what's wrong, and loses nothing. The checks afterwards are in `tests/pi/test_failure_checks.py`; none of them changes
anything.

Before each one, check the Pi is ready and take a note of what's there (kept on your Mac, so it still works when
the Pi's clock is wrong). **On your Mac**:

```bash
DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test
```

```bash
DESKTALK_PI=pi DESKTALK_PI_NOTE_BEFORE=1 uv run pytest -m pi -v -rs -k note_before
```

The note counts for 24 hours. After each one, the command it gives also runs `nothing_deleted`: no event-log day
and no object-state file has gone since the note, apart from days old enough for the usual clean-up. Plug in the USB
speaker first, to hear what Lens says.

#### 1. Power cut and power back

**Setup**: the note, then a cup on the desk for 2 minutes.

**Steps**: as in [A power cut](#a-power-cut): pull the plug without shutting down, plug it back in within about 3
minutes, touch nothing, wait 3 minutes.

**What should happen**: `desktalk.service` starts the three containers once Docker is up, with no network needed; they
become healthy by themselves; the event log and the object-state database can be read; saved states don't come back
until the clock has synced; nothing is deleted. **On the Pi**, `journalctl -b -1 -n 30 --no-pager` shows the last
lines before the cut, with no shut-down in them.

**Afterwards**, **on your Mac**, within 30 minutes of plugging it in:

```bash
DESKTALK_PI=pi DESKTALK_PI_JUST_POWER_CUT=1 uv run pytest -m pi -v -rs -k "starts_after_power_cut or nothing_deleted"
```

#### 2. The network off

**Setup**: the note. The Pi stays on the whole time: this is the network going, not the power.

**Steps**:

1. Switch off your Wi-Fi router, or use the `nmcli` command from [No network at all](#no-network-at-all). On a network
   cable, pull the cable instead (safe with the power on).
2. After a minute or two, put your hand over the camera's lens for 10 seconds. You should hear Lens.
3. Wait 10 minutes from when the network went, then bring it back.
4. Wait until **on your Mac** `ssh pi uptime` answers.

**What should happen**: nothing restarts; the camera keeps taking pictures and the event log keeps filling; Lens speaks
with no network; a linked cloud server gets the events later.

**Afterwards**, **on your Mac**:

```bash
DESKTALK_PI=pi DESKTALK_PI_JUST_NO_NETWORK=1 uv run pytest -m pi -v -rs -k "after_the_network_was_off or nothing_deleted"
```

It checks no container restarted, the camera never went more than 90 seconds without new pictures, and
NetworkManager's journal shows the network really went.

#### 3. A pulled camera cable

**Never take the camera cable out or put it in with the power on.** So this is a start with no camera, then a start
with it back.

**Setup**: the note.

**Steps**:

1. **On the Pi**, `sudo shutdown -h now`. Wait until the green light stops, then pull the plug.
2. Open the latch on the Pi's camera connector and take the cable out. Leave the other end in the camera.
3. Plug the Pi back in. Wait 3 minutes, and listen.
4. **On your Mac**, the first check below.
5. Shut down again, pull the plug, put the cable back (all the way in, straight, latch closed), and plug it back in.
   Wait 3 minutes.
6. **On your Mac**, the second check below.

**What should happen**: with the cable out, the app and the detector are healthy; the camera container keeps starting
and stopping, its log saying "Camera problem: the Pi camera module could not start ... Check the ribbon cable";
in `/api/health`, `checks` shows `camera` as `serious`; after 2 minutes Lens says "I haven't had a single picture from the
Raspberry Pi camera since I started". With the cable back, everything starts by itself and pictures come.

**Afterwards**, **on your Mac**, with the cable out (step 4):

```bash
DESKTALK_PI=pi DESKTALK_PI_CAMERA_CABLE_OUT=1 uv run pytest -m pi -v -rs -k "camera_cable_out or nothing_deleted"
```

and with it back (step 6):

```bash
DESKTALK_PI=pi DESKTALK_PI_CAMERA_CABLE_BACK=1 uv run pytest -m pi -v -rs -k "camera_cable_back or nothing_deleted"
```

#### 4. A full SD card

A big empty file takes the room. `fallocate` sets it aside without writing anything, so it doesn't wear the card, and
as your own user (no `sudo`) it can't take the room the card keeps back for the system. It fills the rest of the card,
not Desk Talk's own space.

**Setup**: the note. Don't run `update.sh` while the card is filled.

**Steps**:

1. **On the Pi**, fill the card until 1500 MB are free (under the 2000 MB warning level):

   ```bash
   free=$(df -m --output=avail ~ | tail -n 1); want=1500; fallocate -l $(( free - want ))M ~/desktalk-fill && df -h ~
   ```

2. Wait a minute and listen: Lens should say the rest of the SD card is getting full, and that Desk Talk is still
   saving.
3. **On your Mac**, the check below.
4. To try the serious level too, **on the Pi**, make the file again for 400 MB free, wait a minute and run the check
   again:

   ```bash
   rm ~/desktalk-fill; free=$(df -m --output=avail ~ | tail -n 1); want=400; fallocate -l $(( free - want ))M ~/desktalk-fill && df -h ~
   ```

5. To undo, **on the Pi**: `rm ~/desktalk-fill`, then `ls ~/desktalk-fill` should say "No such file". Lens should
   say there's enough room again. Then **on your Mac**,
   `DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test`.

**What should happen**: a spoken warning (and a more serious one under 500 MB) that says Desk Talk is still saving;
nothing restarts; pictures keep coming and the event log keeps filling; `card_rest` says `warning` (then `serious`),
while `card` and `saving` stay `ok`; nothing of yours is deleted to make room.

**Afterwards**, **on your Mac**, after step 2 (and step 4):

```bash
DESKTALK_PI=pi DESKTALK_PI_CARD_FILLED=1 uv run pytest -m pi -v -rs -k "card_filled or nothing_deleted"
```

#### 5. The clock set back to 1990

Shows the app catches a clock that is plainly wrong (before `clock.earliest_believable_date`) by itself, even though
the Pi's "synced" flag stays.

**Setup**: the note, then a cup on the desk for 2 minutes, so there are fresh saved states that mustn't come back.

**Steps**:

1. **On the Pi**, stop network time, set the clock back, and restart the app so it starts with the wrong clock:

   ```bash
   sudo timedatectl set-ntp false; sudo date -s '1990-01-01 12:00'
   ```

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose restart app
   ```

2. Wait 2 minutes.
3. **On your Mac**, the first check below, while the clock is still in 1990.
4. To undo, **on the Pi**: `sudo timedatectl set-ntp true`, then wait until `timedatectl` says
   `System clock synchronized: yes` (a minute or so).
5. **On your Mac**, the second check below.

**What should happen**: the app's log says "The clock says 1990-01-01, before clock.earliest_believable_date
(2025-01-01), so it must be wrong", and it picks up no saved states; nothing is deleted by date; the camera and the
detector don't restart; pictures keep coming; the timed jobs keep running. Object states saved meanwhile have
`clock_synced` false, and the event log gets a `1990-01-01` day. After the undo everything carries on with nothing
restarted. **On the Pi**, `rm -r ~/desk-talk/data/events/1990-01-01` takes that day away, if you like.

**Afterwards**, **on your Mac**, with the clock still in 1990 (step 3):

```bash
DESKTALK_PI=pi DESKTALK_PI_CLOCK_1990=1 uv run pytest -m pi -v -rs -k "clock_set_to_1990 or nothing_deleted"
```

and after the undo (step 5):

```bash
DESKTALK_PI=pi DESKTALK_PI_CLOCK_BACK=1 uv run pytest -m pi -v -rs -k "clock_back or nothing_deleted"
```

#### 6. Desk Talk's own space missing

Desk Talk's space (`~/desk-talk/data.img`) is mounted on `data/` by `desktalk-data-room.service`. If it isn't mounted,
the app must not write to the card's bare `data/` folder: it saves nothing, says so out loud, and leaves the history
alone.

**Setup**: the note, then a cup on the desk for 2 minutes.

**Steps**:

1. **On the Pi**, take the space away and restart the app:

   ```bash
   sudo systemctl stop desktalk-data-room
   ```

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose restart app
   ```

2. Wait a minute and listen: Lens should say nothing is being saved right now.
3. **On the Pi**, `ls ~/desk-talk/data` should show an empty folder.
4. **On your Mac**, the first check below.
5. To undo, **on the Pi**:

   ```bash
   sudo systemctl start desktalk-data-room
   ```

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose restart app
   ```

   `ls ~/desk-talk/data` shows the history again, and Lens stops warning.
6. **On your Mac**, the second check below.

**What should happen**: the live view and warnings still work; in `/api/health`, `checks` shows `saving` as `serious`
while the space is away and `ok` after; nothing is written to the bare `data/` folder; the history is all there after
the undo.

**Afterwards**, **on your Mac**, with the space still away (step 4):

```bash
DESKTALK_PI=pi DESKTALK_PI_SPACE_MISSING=1 uv run pytest -m pi -v -rs -k space_missing
```

and after the undo (step 6):

```bash
DESKTALK_PI=pi DESKTALK_PI_SPACE_BACK=1 uv run pytest -m pi -v -rs -k "space_back or nothing_deleted"
```

### Last results

| Check | Last run | Result |
|---|---|---|
| A power cut | 2026-11-10 | passed, with the clock 10 hours behind until it synced |
| A full SD card | 2026-11-14 | passed, including a 6-hour run with the rest of the card at 400 MB free |
| The clock set back to 1990 | 2026-11-14 | passed |
| Desk Talk's own space missing | 2026-11-14 | passed |

The rest haven't been recorded yet. Add a row when you run one.
