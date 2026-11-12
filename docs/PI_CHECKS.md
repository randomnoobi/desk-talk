# Checks on the real Pi

`uv run pytest -m pi` runs against the real Pi over SSH. Most of it runs alone. The rest needs a person, so those tests skip and say which part of this page to do. This page has the steps for each, and what to run afterwards.

When something is wrong for real and needs a person (the camera, the SD card, the clock, the heat), [RUNBOOK.md](RUNBOOK.md) says what to check and what to do.

## The tests that run alone

They stop and start containers, and put everything back afterwards, even if a test fails. They take about 15 minutes.

**On your Mac**, from the project folder:

```bash
DESKTALK_PI=pi uv run pytest -m pi -v -rs
```

- `DESKTALK_PI` is what you'd type after `ssh`. The SSH login has to use a key: the tests never ask for a password.
- If the Pi's compose file isn't in `~/desk-talk/raspberry_pi`, add `DESKTALK_PI_DIR='~/your-folder/raspberry_pi'`. Keep the quotes, so `~` means the Pi's home and not your Mac's.
- A skip isn't a pass. It means nobody has checked that part yet.

Two tests stand in for things only a person can do, and neither replaces them:

- `test_pictures_reach_the_app_and_the_detector_answers_for_each` shows pictures flowing and the detector answering. It doesn't show that an object gets noticed. "A cup on the desk" below does.
- `test_all_three_killed_come_back_readable` kills all three containers, but the Pi keeps running, so everything already written still reaches the card. It isn't a power cut. "A power cut" below is.

`test_disk_stays_under_limit` cuts `pi.delete_every_min` to 1 minute with a throwaway copy of the settings, and takes it away again at the end. It doesn't fill the disk, so deleting early at `pi.min_free_mb` is only covered by unit tests.

## A cup on the desk

Shows that a real object turns into an `object_state` event with only the Pi running.

1. Point the camera at the desk, with the cup out of view. Nothing else needs to run: the Mac only reads the result over SSH.
2. **On your Mac**, start the test:

   ```bash
   DESKTALK_PI=pi DESKTALK_PI_OBJECT_ON_DESK=1 uv run pytest -m pi -v -rs -k test_works_with_nothing_else_on
   ```

3. Put the cup in view within 3 minutes, and leave it there.

It passes when the event shows up. A cup put down in the 5 minutes before the test also counts.

## A power cut

Shows the Pi starts everything by itself, and that pulling the plug leaves the event log and SQLite readable. Done on a real Pi on 2026-11-10: it passed, with the clock 10 hours behind until it synced.

1. Put a cup on the desk and wait 2 minutes, so its state gets saved.
2. Pull the Pi's power plug. Don't shut it down first.
3. Plug it back in within about 3 minutes. If the clock syncs before the app starts, saved states older than `object_states.restore_max_age_sec` (5 minutes) start over on purpose, and the boot takes some of that.
4. Touch nothing on the Pi. Wait 3 minutes.
5. **On your Mac**, within 30 minutes of plugging it in:

   ```bash
   DESKTALK_PI=pi DESKTALK_PI_JUST_POWER_CUT=1 uv run pytest -m pi -v -rs -k test_starts_after_power_cut
   ```

It checks that the Pi has only just booted, that all three are healthy and started by themselves soon after the boot, that pictures are coming in, that the event log and SQLite can be read, and that the app picked up the right saved states at this start: none while the clock hadn't synced. If the clock had synced first, it skips that part when nothing was saved just before the cut, and says so.

The kernel can number the camera's kinds of device (`media`, `dma_heap`) differently at any boot, so a camera container made with the old numbers can't open the camera. `desktalk.service` writes this boot's numbers into `.env` (`device_numbers.sh`) before it starts the containers, and makes the camera container again when one changed. A Pi set up before that gets the new `desktalk.service` from `bash raspberry_pi/update.sh`, which asks for sudo once to put it in. `test_the_camera_container_may_open_the_devices_this_boot_numbered` and `test_the_service_writes_the_device_numbers_before_starting_the_containers` in `tests/pi/test_real_pi_start_on_boot.py` check that at any boot, power cut or not (the command is in "1. Power cut and power back" below). `test_starts_after_power_cut` catches it too, because it waits for pictures.

It also checks the power really was cut, so a `sudo reboot` fails on purpose. The Pi's journal is kept in memory, so the boot before this one is normally gone (Raspberry Pi OS sets `Storage=volatile`). The proof is then the app's log: a clean shut-down lets the app write `Shutting down` before it stops, and a pulled plug doesn't. Stopping the containers yourself first also fails it, for the same reason.

The Pi has no clock battery, so after a power cut its clock starts behind and jumps forward once it reaches the internet. So until the Pi says its clock has been set from the internet (the file `/run/systemd/timesync/synchronized`, which compose.yaml lets the app see), the app brings back only states saved since this boot: each saved state keeps the Pi's boot id (`/proc/sys/kernel/random/boot_id`), which is new after every boot. After a power cut the app almost always starts before the clock is set, so it says its clock hasn't synced, picks up nothing from before the cut, and the check expects exactly that. It reads the boot id the app sees, so if the app was restarted again since the boot, what that boot saved may come back, and the check allows for it. The check works out when the app really started from the Pi's uptime, not from Docker's start time, because Docker took that from the wrong clock. Everything else it reads by the app's own clock: the app's log tells which run is this one by the order of its lines, not their times, and the saved states are compared with the time on this run's `Picked up` line. If the app's clock was still behind when it restored and the app didn't say so, the check fails.

## An app restart before the clock has synced

Shows that restarting only the app (as the watchdog or the memory limit does) keeps its saved states on a Pi that hasn't reached the internet since it booted. The clock ran on without a break since the boot, so what this boot saved is still good. A power cut is the other case, above.

The Pi must be on the Wi-Fi, so the test can reach it, but must not have set its clock from the internet since it booted. Either:

- **With a router:** unplug the router's internet cable (keep the router itself on), then pull the Pi's plug and put it back. Plug the internet cable back in only after the test.
- **Without one:** **on the Pi**, `sudo systemctl stop systemd-timesyncd && sudo rm -f /run/systemd/timesync/synchronized`. This only pretends: the clock is still right. Afterwards, **on the Pi**, `sudo systemctl start systemd-timesyncd` (it makes the file again once it reaches the internet).

Then:

1. **On your Mac**, check the Pi's clock hasn't synced: `ssh pi ls /run/systemd/timesync/` must not list `synchronized`.
2. Put a cup on the desk and wait 2 minutes, so its state gets saved.
3. **On your Mac**:

   ```bash
   DESKTALK_PI=pi uv run pytest -m pi -v -rs -k test_app_restart_before_the_clock_has_synced
   ```

It restarts the app, then checks the app said its clock hadn't synced, and still picked up what this boot saved in the last `object_states.restore_max_age_sec` (the cup). It skips if the clock has synced, or if nothing was saved.

## No Wi-Fi at boot

Shows everything starts and works with no network there at all.

1. Switch off your Wi-Fi router.
2. Pull the Pi's plug and put it back, or shut it down first if you'd rather; only the start matters here.
3. Wait 3 minutes. Then switch the router back on, and wait until the Pi is back on the Wi-Fi: **on your Mac**, `ssh pi uptime` answers.
4. **On your Mac**, within 30 minutes of the boot:

   ```bash
   DESKTALK_PI=pi DESKTALK_PI_NO_WIFI_AT_BOOT=1 uv run pytest -m pi -v -rs -k test_no_wifi_at_boot
   ```

It checks that all three started by themselves and the data can be read, as after a power cut, but not how the Pi was switched off.

The proof the Wi-Fi was off is the journal: the time of NetworkManager's first `device (wlan0): Activation: successful` must be later than each container's start. **On the Pi**, this shows that line, with the seconds since boot at the start of it (your user must be in the `adm` group, as the first user is):

```bash
journalctl -b -u NetworkManager -o short-monotonic --no-pager | grep -m1 "device (wlan0): Activation: successful"
```

The test fails if the Wi-Fi came first, as the run then shows nothing. It skips and says so if the journal doesn't have the line.

## No network at all

Shows that with no Wi-Fi and no AI the app keeps recording, and that the speak command runs. Nothing can reach the Pi during this, so a script on the Pi takes notes in `data/pi_checks/no_network.json`, and a test reads them once the Wi-Fi is back.

1. To check speech as well, plug in the USB speaker first.
2. **On your Mac**, start the script from the Pi's project folder. It waits up to 15 minutes for the network to go:

   ```bash
   ssh pi 'cd ~/desk-talk || exit 1; nohup python3 tests/pi/on_the_pi.py no-network > ~/no_network_check.log 2>&1 < /dev/null & sleep 3; cat ~/no_network_check.log; grep -q Started ~/no_network_check.log'
   ```

   It should print `Started: waiting for the network to go.` If it prints anything else, or says there's no such folder, **stop here**: it isn't running, and switching off the router would only waste your time. Use your folder in place of `~/desk-talk` if it's somewhere else.

3. Switch off your Wi-Fi router. The SSH connection and the page stop working, which is expected.

   With no router to switch off, do this **on the Pi** with a keyboard and screen instead (it asks for your password, and brings the Wi-Fi back by itself after 10 minutes):

   ```bash
   sudo sh -c 'nmcli radio wifi off; sleep 600; nmcli radio wifi on'
   ```

4. After a minute or two, put your hand over the camera's lens for 10 seconds, so Lens has something to say. If you have a speaker in, you should hear it.
5. Wait 7 minutes from when the Wi-Fi went off. The script restarts the app once in that time, on purpose.
6. Switch the router back on. If the Wi-Fi comes back too soon the run says so, and you do it again.
7. **On your Mac**, once `ssh pi uptime` answers:

   ```bash
   DESKTALK_PI=pi uv run pytest -m pi -v -rs -k no_network
   ```

`test_runs_with_no_network_at_all` reads the notes. `test_speak_command_ran_with_no_network` looks for the app's "Said a message out loud" line in those notes. It skips if no speaker was in or nothing was said. The notes count for 24 hours.

## Listen to the speaker

No test can tell if speech sounds right, so this is by ear.

1. Plug the USB speaker in while the app is running. Don't restart anything.
2. Put your hand over the camera's lens for 10 seconds.
3. Listen: do you hear Lens within a few seconds, and can you understand the words?
4. **On the Pi**, see that the app noticed:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose logs --since 5m app | grep "out loud"
   ```

   You should see `Said a message out loud (Lens)`. `No sound card with "usb" in its name` means it didn't find the speaker, and `Could not say a message out loud` says why it failed.

A webcam with a microphone can get picked ahead of the speaker, as its name has `usb` in it too. If so, set `notifications.sound_card` in `raspberry_pi/mine/settings.mine.yaml` to a word only the speaker's name has: **on the Pi**, `cat /proc/asound/cards` lists them.

## Close the laptop while chat is on

Only for a Pi with `llm.ai_url` pointing at an AI on your laptop, and `llm.ai_on_my_computer: true`. Shows warnings don't wait for an AI that has gone away.

1. Send a chat message from the page and see the AI answer.
2. Close the laptop, and wait until it has gone to sleep.
3. Put your hand over the camera's lens for 10 seconds.
4. The warning should come in its ready-made words within a few seconds, on the speaker and on the page from a phone.
5. Send a chat message. It may take up to `llm.ollama_timeout_sec` (30 seconds on the Pi) to say the AI can't be reached, but the page must keep working.
6. Open the laptop. Chat should work again with nothing restarted.

## Open the page from a real phone

1. Have the phone on the same Wi-Fi as the Pi, not on mobile data.
2. Open `http://desktalk.local:5000` in its browser. If that name doesn't work, use the Pi's address: **on the Pi**, `hostname -I` shows it.
3. See the live picture move. Put a cup in view and see it listed under "On the desk".
4. Add a to-do and press a habit button. Reload the page: both are still there.
5. Turn the phone sideways, and see that nothing gets cut off.

## Failure hand tests

Five things that go wrong in real life, done on purpose: the power cut and back, the network off, a pulled cable, a
full SD card, and the clock set back to 1990. Each shows the Pi gets through by itself, or says what's wrong, and
loses nothing. When one fails, [RUNBOOK.md](RUNBOOK.md) says what to do about it for real. The checks afterwards are in
`tests/pi/test_failure_checks.py`; none of them breaks or changes anything.

**Before each one**, check the Pi is ready, then take a note of what's there (it's kept on your Mac, so it still works
when the Pi's clock is wrong). **On your Mac**:

```bash
DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test
```

```bash
DESKTALK_PI=pi DESKTALK_PI_NOTE_BEFORE=1 uv run pytest -m pi -v -rs -k note_before
```

The note counts for 24 hours. **After each one**, the command it gives also runs `nothing_deleted`: no event-log day and
no object-state file has gone since the note, apart from days old enough for the usual clean-up
(`event_log.keep_days`). Plug in the USB speaker first, to hear what Lens says.

Write down each run in the line at the end of its part: the date, passed or failed, and anything odd.

### 1. Power cut and power back

**Setup**: the note, then a cup on the desk for 2 minutes.

**Steps**: as in "A power cut" above: pull the Pi's plug without shutting it down, plug it back in within about 3
minutes, touch nothing, wait 3 minutes.

**What should happen**: `desktalk.service` starts the three containers once Docker is up, with no network needed; they
become healthy by themselves; the event log and the object-state database can be read; saved states don't come back
until the clock has synced; nothing is deleted. The journal now keeps the boot before: **on the Pi**,
`journalctl -b -1 -n 30 --no-pager` shows the last lines before the cut, with no shut-down in them.
The camera container may open the camera's devices by the numbers the kernel gave them at this boot, which can
change at any boot: `desktalk.service` writes them into `.env` before it starts the containers.
`tests/pi/test_real_pi_start_on_boot.py` checks the service, the journal settings and those numbers on their own, at
any time: `DESKTALK_PI=pi uv run pytest -m pi -v -rs tests/pi/test_real_pi_start_on_boot.py`.

**Afterwards**, **on your Mac**, within 30 minutes of plugging it in:

```bash
DESKTALK_PI=pi DESKTALK_PI_JUST_POWER_CUT=1 uv run pytest -m pi -v -rs -k "starts_after_power_cut or nothing_deleted"
```

Result: _date, passed / failed, notes_

### 2. The network off

**Setup**: the note. The Pi has to stay on the whole time: this is the network going, not the power.

**Steps**:

1. Switch off your Wi-Fi router. With no router to switch off, **on the Pi** with a keyboard and screen (the Wi-Fi
   comes back by itself after 10 minutes):

   ```bash
   sudo sh -c 'nmcli radio wifi off; sleep 600; nmcli radio wifi on'
   ```

   If your Pi uses a network cable, pull the cable instead. That's safe with the power on. (This Pi uses the Wi-Fi:
   **on the Pi**, `ip route show default` names `wlan0`.)
2. After a minute or two, put your hand over the camera's lens for 10 seconds. You should hear Lens.
3. Wait 10 minutes from when the network went, then switch the router back on (or put the cable back).
4. Wait until **on your Mac** `ssh pi uptime` answers.

**What should happen**: nothing restarts; the camera keeps taking pictures all the way through and the event log keeps
filling; Lens speaks on the speaker with no network; a cloud server, if linked, gets the events later, its tries waiting
longer each time, with a random part (`cloud.retry_jitter`).

**Afterwards**, **on your Mac**:

```bash
DESKTALK_PI=pi DESKTALK_PI_JUST_NO_NETWORK=1 uv run pytest -m pi -v -rs -k "after_the_network_was_off or nothing_deleted"
```

It checks the Pi didn't restart and no container did, that the camera never went more than 90 seconds without new
pictures, and that NetworkManager's journal shows the network really went (your user must be in the `adm` group to read
it, as the first user is). "No network at all" above is the longer one: it also restarts the app with no network.

Result: _date, passed / failed, notes_

### 3. A pulled cable: the camera's ribbon cable

**Never take the camera cable out or put it in with the power on.** It can break the camera or the Pi. So this is a
start with no camera, then a start with it back.

**Setup**: the note.

**Steps**:

1. **On the Pi**, `sudo shutdown -h now`. Wait until the green light stops, then pull the plug.
2. Open the latch on the Pi's camera connector and take the cable out. Leave the other end in the camera.
3. Plug the Pi back in. Wait 3 minutes, and listen.
4. **On your Mac**, the first check below.
5. **On the Pi**, `sudo shutdown -h now` again, pull the plug, put the cable back (all the way in, straight, latch
   closed), and plug it back in. Wait 3 minutes.
6. **On your Mac**, the second check below.

**What should happen**: with the cable out, the app and the detector start and are healthy; the camera container keeps
starting and stopping, its log saying "Camera problem: the Pi camera module could not start ... Check the ribbon
cable"; `/api/health` → `checks` → `camera` says `serious`; after 2 minutes (`lens.wait_before_saying_sec.no_pictures`)
Lens says "I haven't had a single picture from the Raspberry Pi camera since I started", on the speaker and on the
page.
With the cable back, everything starts by itself and pictures come. Nothing is deleted.

**Afterwards**, **on your Mac**, with the cable out (step 4):

```bash
DESKTALK_PI=pi DESKTALK_PI_CAMERA_CABLE_OUT=1 uv run pytest -m pi -v -rs -k "camera_cable_out or nothing_deleted"
```

and with it back (step 6):

```bash
DESKTALK_PI=pi DESKTALK_PI_CAMERA_CABLE_BACK=1 uv run pytest -m pi -v -rs -k "camera_cable_back or nothing_deleted"
```

A USB webcam can simply be unplugged and plugged in again with the power on; the same checks don't cover that.

Result: _date, passed / failed, notes_

### 4. A full SD card

A big empty file takes the room. `fallocate` sets the room aside at once without writing anything, so it doesn't wear
the card, and as your own user (no `sudo`) it can't take the room the card keeps back for the system. The file goes in
your home folder, never in the data folder.

**Setup**: the note. Don't run `update.sh` while the card is filled.

**Steps**:

1. **On the Pi**, fill the card until 1500 MB are free (under the 2000 MB warning level,
   `health_checks.card_warning_below_mb`; the serious one is `card_serious_below_mb`, 500 MB):

   ```bash
   free=$(df -m --output=avail ~ | tail -n 1); want=1500; fallocate -l $(( free - want ))M ~/desktalk-fill && df -h ~
   ```

   `df` should show about 1.5 GB free.
2. Wait a minute and listen: Lens looks at the card every 2 seconds (`lens.check_every_sec`) and should say "The SD
   card is getting full, only ... MB left."
3. **On your Mac**, the check below.
4. To try the serious level too, **on the Pi**, make the file again for 400 MB free (under 500 MB), wait a minute
   ("The SD card is almost full") and run the check again:

   ```bash
   rm ~/desktalk-fill; free=$(df -m --output=avail ~ | tail -n 1); want=400; fallocate -l $(( free - want ))M ~/desktalk-fill && df -h ~
   ```

5. **Undo**, **on the Pi**: `rm ~/desktalk-fill`, and `df -h ~` shows the room back. Lens should say it's fine again.
   Then **on your Mac**, `DESKTALK_PI=pi uv run pytest -m pi -v -rs -k ready_for_a_hand_test`.

**What should happen**: a spoken warning (and a more serious one under 500 MB); nothing restarts; pictures keep coming
and the event log keeps filling; nothing of yours is deleted to make room: the caps on the data folder go by age and
count, not by the card's free space (`event_log.keep_days` and `object_states.keep_days`, 30 days; the story pictures,
a set number per object). If the card fills right up, the object states that can't be saved wait in memory, at most
`object_states.unsaved_most` (5000). The check also reads `/api/health` → `checks` → `card`, which should say
`warning` (or `serious`) by the app's own levels.

**Afterwards**, **on your Mac**, after step 2 (and step 4):

```bash
DESKTALK_PI=pi DESKTALK_PI_CARD_FILLED=1 uv run pytest -m pi -v -rs -k "card_filled or nothing_deleted"
```

Result: 2026-11-11, passed. At 1500 MB free the card check said `warning`, and at 400 MB `serious`; Lens said each, and said "enough space ... again" after the undo. Nothing restarted. The first try failed only because the check let no line of the event log be broken, and a power cut on 2026-11-03 left one (zero bytes, in the middle of that day's file): it now allows one, as the other checks do.

### 5. The clock set back to 1990

The Pi's flag that says its clock was set from the internet (`/run/systemd/timesync/synchronized`) stays there when
network time is turned off, so this shows the app catching a clock that is plainly wrong by itself: before
`clock.earliest_believable_date` (2025-01-01). A clock before it counts as one that hasn't synced, and deletes
nothing by date.

**Setup**: the note, then a cup on the desk for 2 minutes, so there are fresh saved states that mustn't come back.

**Steps**:

1. **On the Pi**, stop network time and set the clock back, then restart the app so it starts with the wrong clock:

   ```bash
   sudo timedatectl set-ntp false; sudo date -s '1990-01-01 12:00'
   ```

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose restart app
   ```

2. Wait 2 minutes.
3. **On your Mac**, the first check below, while the clock is still in 1990.
4. **Undo**, **on the Pi**:

   ```bash
   sudo timedatectl set-ntp true
   ```

   Then wait until `timedatectl` says `System clock synchronized: yes` (a minute or so).
5. **On your Mac**, the second check below.

**What should happen**: with the clock in 1990, the app's log says "The clock says 1990-01-01, before
clock.earliest_believable_date (2025-01-01), so it must be wrong" and it picks up no saved states; nothing is deleted
by date (the event log's old days and old object states stay); the camera and the detector don't restart; pictures
keep coming; the timed jobs (saving states, compacting the event log) keep running, as they go by the time since boot.
What happens in 1990 is saved as 1990: each object state saved then is marked with `clock_synced` false, and the
event log gets a `1990-01-01` day.
After the undo, everything carries on with nothing restarted. The `1990-01-01` day can stay, or **on the Pi**,
`rm -r ~/desk-talk/data/events/1990-01-01` takes it away.

**Afterwards**, **on your Mac**, with the clock still in 1990 (step 3):

```bash
DESKTALK_PI=pi DESKTALK_PI_CLOCK_1990=1 uv run pytest -m pi -v -rs -k "clock_set_to_1990 or nothing_deleted"
```

and after the undo (step 5):

```bash
DESKTALK_PI=pi DESKTALK_PI_CLOCK_BACK=1 uv run pytest -m pi -v -rs -k "clock_back or nothing_deleted"
```

Result: _date, passed / failed, notes_
