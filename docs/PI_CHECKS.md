# Checks on the real Pi

`uv run pytest -m pi` runs against the real Pi over SSH. Most of it runs alone. The rest needs a person, so those tests skip and say which part of this page to do. This page has the steps for each, and what to run afterwards.

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

Shows the Pi starts everything by itself, and that pulling the plug leaves the event log and SQLite readable.

1. Put a cup on the desk and wait 2 minutes, so its state gets saved.
2. Pull the Pi's power plug. Don't shut it down first.
3. Plug it back in within about 3 minutes. Saved states older than `object_states.restore_max_age_sec` (5 minutes) start over on purpose, and the boot takes some of that.
4. Touch nothing on the Pi. Wait 3 minutes.
5. **On your Mac**, within 30 minutes of plugging it in:

   ```bash
   DESKTALK_PI=pi DESKTALK_PI_JUST_POWER_CUT=1 uv run pytest -m pi -v -rs -k test_starts_after_power_cut
   ```

It checks that the Pi has only just booted, that all three are healthy and started by themselves soon after the boot, that pictures are coming in, that the event log and SQLite can be read, and that the app picked up the saved states at this start. It skips the last part if nothing was saved just before the cut, and says so.

It also checks the power really was cut, so a `sudo reboot` fails on purpose. The Pi's journal is kept in memory, so the boot before this one is normally gone (Raspberry Pi OS sets `Storage=volatile`). The proof is then the app's log: a clean shut-down lets the app write `Shutting down` before it stops, and a pulled plug doesn't. Stopping the containers yourself first also fails it, for the same reason.

The clock can be wrong after a power cut with no clock battery. That's known, and not part of this check.

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
4. **On the Pi**, in the compose folder, see that the app noticed:

   ```bash
   docker compose logs --since 5m app | grep "out loud"
   ```

   You should see `Said a message out loud (Lens)`. `No sound card with "usb" in its name` means it didn't find the speaker, and `Could not say a message out loud` says why it failed.

A webcam with a microphone can get picked ahead of the speaker, as its name has `usb` in it too. If so, set `notifications.sound_card` in `raspberry_pi/mine/settings.mine.yaml` to a word only the speaker's name has: **on the Pi**, `cat /proc/asound/cards` lists them.

## Close the laptop while chat is on

Only for a Pi with `llm.ai_url` pointing at an AI on your laptop. Shows warnings don't wait for an AI that has gone away.

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
