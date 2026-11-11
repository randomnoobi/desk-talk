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

   <!-- check: task A (script name, its options, and where it saves the file) -->

   It saves the last hours of each container's log, the list of boots and the health of each container. **On the
   Pi**, `bash ~/desk-talk/raspberry_pi/get_logs.sh` does the same and leaves the file on the Pi.
   <!-- check: task A (get_logs.sh name and what it saves) -->

2. **On the Pi**, see what's running:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose ps
   ```

   All three (`app`, `camera`, `detector`) should say `healthy`. `Restarting` means it starts, fails and starts again.

3. **On the Pi**, the health of each measure, as the app sees it (the camera, new detections, the detector, the
   temperature, the SD card and the Pi's heartbeat):

   ```bash
   curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool | less
   ```

   <!-- check: task C (the names of the measures in /api/health) -->

The logs live in the Pi's journal, which keeps at most 200 MB and nothing older than 30 days, and survives a restart.
**On the Pi**, `journalctl -b -1` shows the boot before this one: the lines just before a power cut or a crash.
<!-- check: task A (journal kept on the card, 200 MB, 30 days; containers log to the journal) -->

Desk Talk itself starts with the Pi: `desktalk.service` runs `docker compose up -d` once Docker is up. **On the Pi**,
`systemctl status desktalk.service` shows whether it ran. <!-- check: task A (unit name) -->

## The camera isn't seen, or keeps restarting

**What you notice**

- Lens says: "I lost the pictures from the Raspberry Pi camera." The live view on the page stops.
- Or, after the Pi starts, there are no pictures at all, and Lens says the camera isn't sending any.
  <!-- check: task C (the warning for no pictures from the camera, also when none came since the start) -->
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
- Or Lens says nothing new has been found for a while, though the camera is sending pictures: the detector is so busy
  it never answers. <!-- check: task C (the "no new detections" warning, after 300 s, and its words) -->
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
  itself once it's ready. "The detector has no model yet" or `/health` saying `error`: see "The detector container is
  down" in [raspberry_pi/README.md](../raspberry_pi/README.md).
- **Busy all the time**: give it less to do. **On the Pi**, in `raspberry_pi/mine/settings.mine.yaml`, a smaller
  picture size (`pi.detector.image_size`) or fewer pictures a second (`pi.fps`), then
  `cd ~/desk-talk/raspberry_pi && docker compose restart app camera`. After many failures in a row the camera stops
  asking for a while, then tries again, waiting a little longer each time (with a random part, so it doesn't try
  at the same moment as the app). <!-- check: task B (jittered waits and the breaker in the camera's detector client) -->

**When to replace hardware**

Never for this alone. If it only happens when the Pi is hot, fix the cooling.

## The SD card is nearly full

**What you notice**

- Lens says the SD card is nearly full: a warning under 2000 MB free, and a more serious one under 500 MB.
  <!-- check: task C (the SD card measure, its levels and its words) -->
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

1. **On your Mac**, make a backup first: `bash scripts/backup_pi.sh`. <!-- check: task E (script name) -->
2. **On the Pi**, clear Docker's saved build steps and old images. Nothing of yours is in them; the next rebuild just
   takes longer (12 to 21 minutes):

   ```bash
   docker builder prune -f && docker image prune -f
   ```

   `update.sh` does this after each rebuild, keeping a set amount. <!-- check: task E (update.sh prunes the build cache, and the setting for how much it keeps) -->
3. The journal keeps itself under 200 MB, the event log keeps `event_log.keep_days` (30) days, and the object-state
   database keeps a set number of days. <!-- check: task E (the object-state database cap and its setting) -->
   If the data folder is still big, look at what `du` showed before deleting anything, and ask first.
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

1. **Back up now, while it still reads.** **On your Mac**: `bash scripts/backup_pi.sh`.
   <!-- check: task E (script name; that it copies the database through SQLite's backup) -->
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
   <!-- check: task E (script name) -->
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

   It installs Docker, the start-on-boot service and the journal settings, fetches the model and starts the three
   containers. If it says the Pi needs a restart, **on the Pi**, `sudo reboot`.
   <!-- check: task A (install.sh installs and enables desktalk.service and the journald file) -->
6. Restore the backup: the next part.
7. **On your Mac**, check it: `DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test`.
8. **On your Mac**, the weekly backup keeps working with no change, as long as the name stays the same.
   <!-- check: task E (the launchd job uses `ssh pi`) -->

## Restoring from a backup

A backup is a dated folder on your Mac with the Pi's data folder (`data/`: the event log, the object states, habits,
to-dos) and your own settings (`settings.mine.yaml`). `scripts/backup_pi.sh` makes one every week, and keeps the last
few. <!-- check: task E (where the backups are kept, the folder names inside one, and how many are kept) -->

The pairing key and the models aren't in it: `install.sh` makes a new key and fetches the model.

1. **On your Mac**, pick the backup, the newest unless you want an older one:

   ```bash
   ls <the backups folder>
   ```

2. **On the Pi**, stop the containers, and put the current data folder aside (nothing is deleted):

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose stop && mv ~/desk-talk/data ~/desk-talk/data.before-restore-$(date +%F)
   ```

3. **On your Mac**, copy the backup to the Pi (put the backup's folder in place of `<backup>`):

   ```bash
   rsync -a "<backup>/data/" pi:desk-talk/data/ && scp "<backup>/settings.mine.yaml" pi:desk-talk/raspberry_pi/mine/settings.mine.yaml
   ```

4. **On the Pi**, start again:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose up -d
   ```

5. **On your Mac**, check: `DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test`. Open the page and
   see your to-dos and habits.

Objects on the desk start over after a restore (the saved states are from another boot), which is expected. Once it
all works, the folder put aside can go: **on the Pi**, `rm -r ~/desk-talk/data.before-restore-*`.

## The clock is wrong after a power cut

**What you notice**

- After a power cut with no internet, the times in the log and on the page are off, often by hours.
- Objects that were on the desk start over instead of picking up where they were.
- Lens says "The Raspberry Pi's clock is ... seconds behind the app's."

**Why**

The Pi 5 has a clock chip, but without its battery it forgets the time when the power goes. Until it reaches the
internet, it starts from about the time it last shut down. See [CLOCK.md](CLOCK.md) for the battery.
<!-- check: task D (docs/CLOCK.md exists and says what to buy) -->

While the clock hasn't been set from the internet, or is plainly wrong (before `clock.earliest_believable_date`), the
app picks up only what this boot saved, marks each saved row with `clock_synced` false, and deletes nothing by date.
Timed jobs go by the time since boot, so a clock jump doesn't hold them up. Once the clock is right everything carries
on; nothing needs a restart.
<!-- check: task D (clock.earliest_believable_date, clock_synced per row, no prune while wrong, monotonic scheduler) -->

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
connector), as [CLOCK.md](CLOCK.md) says. <!-- check: task D -->

## The Pi is too hot

**What you notice**

- Lens says "The Raspberry Pi is running hot (75°C), so it may slow down." at 75 °C, and a more serious warning at
  80 °C. <!-- check: task C (the serious level at 80 °C and its words) -->
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
