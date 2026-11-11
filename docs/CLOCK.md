# The clock

Desk Talk writes the time on everything it saves: each event in the log, each saved object state, each
story picture. On a Mac the clock is always right. On the Pi it can be wrong for a while, and this page
says when, what Desk Talk does about it, and whether to buy the Pi's clock battery.

## Why the Pi's clock can be wrong

The Pi 5 has a clock chip of its own (`/dev/rtc0`), but nothing keeps it running when the power is off
unless a battery is plugged into its battery connector (marked BAT, J5). Ours has none. So:

- **After a power cut** the Pi starts at the last time it saved (Raspberry Pi OS saves it now and then
  and at shutdown), which may be hours or days behind. It puts the clock right once it reaches the
  internet (`systemd-timesyncd`). With no Wi-Fi, it stays behind.
- **By hand**, someone can set the clock to anything, say 1 January 1990 (the meeting's hand test), or next
  year.
- Rarely, a bad time server could set it far ahead.

## What Desk Talk does about it

**Each saved object state says how much its time can be trusted.** Every row in
`data/object_states.sqlite` keeps `boot_id` (which boot of the Pi wrote it) and `clock_synced` (had the
clock been set from the internet when it was written, and was it believable). Rows from before these
columns existed have none (empty). `clock_synced` is asked again for every row, so rows written after the
clock syncs say so.

**A clock that is plainly wrong counts as not synced.** Settings → `clock.earliest_believable_date`
(2025-01-01) is a day before this version of Desk Talk came out. A clock before it (1990) is wrong for
sure. The app says so once in its log ("The clock says 1990-01-01, before clock.earliest_believable_date
..."), and again when it looks right.

**Picking up after a restart.** Desk Talk brings back each object's saved state (on the desk or not) when
it starts, if the state is recent enough (`object_states.restore_max_age_sec`). It only does this when it
can trust the times:

- the clock has synced since the Pi started, and is believable: everything recent comes back;
- not synced yet (or not believable), but only the app restarted: what this same boot saved comes back,
  since the clock ran on without a break since then;
- a saved state newer than the clock: the clock went back, so every object starts over;
- otherwise (a power cut with no internet yet): every object starts over.

**Nothing is deleted for being old while the clock is wrong.** The event log deletes days older than
`event_log.keep_days` (30), and the object states delete rows older than `object_states.keep_days` (30), once an
hour. Neither deletes anything while the clock can't be right:

- **Too early.** A clock before the earliest believable date (1990).
- **Jumped far ahead.** A clock set to next year would make both delete everything older than next year minus
  30 days: the whole history, at once, for good. So the app watches for the clock jumping. The wall clock minus
  the monotonic clock (below) stays the same while time goes on, and changes only when someone or something
  sets the clock. If it moves more than `clock.largest_jump_ahead_min` (60) ahead of what it was, nothing is
  deleted by date, and the app's log says so once ("The clock jumped ... hours ahead ..."). What it is compared
  with is taken when the app starts, and again each time the time server sets the clock: systemd-timesyncd
  touches its flag file (`object_states.clock_synced_file`) every time it does. So the jump that puts the Pi right
  after a power cut, which can be days ahead, is fine: the flag file comes with it. Deleting starts again when the
  time server next sets the clock (back, or to the same time, which then counts as right), when someone sets it
  back by hand, or when the app restarts. A jump back is never a problem: it deletes less, not more. A Mac (no
  flag file) isn't watched: it keeps its own clock right, and its monotonic clock stops while it sleeps, so
  waking up would look like a jump.

What this can't see: a clock set ahead while the app wasn't running (set by hand, then the app restarted
before the time server put it right). The app takes that clock as it finds it. For that, the event log always
keeps the newest 30 days that have a log, whatever the clock says, so it can't lose them all at once; the object
states have no such floor.

**Timers use a clock that only goes forward.** How often jobs run (compacting the event log, saving the
object states, deleting old log days) and how often the Pi deletes its stored pictures go by the
computer's monotonic clock, which counts seconds since boot and never jumps. So setting the wall clock
back doesn't stop them, and setting it forward doesn't rush them.

**What a wrong clock still does:**

- Events are filed under the day the clock says, so a 1990 clock writes into a `data/events/1990-01-01/`
  folder. Once the clock is right, that day is older than 30 days and the next prune deletes it.
- The habit day (water, breaks) rolls over by the clock, so a jump can start a new day early or late.
- Story pictures are kept newest first, so pictures taken with a 1990 clock count as the oldest and are
  the first to go. Pictures taken with a clock far ahead push out at most the usual number per object.
- The Pi's own stored pictures are deleted by the time they were taken. With a 1990 clock, the ones from
  before the change aren't old enough yet, so they stay until the disk gets nearly full, which deletes the
  oldest anyway (`pi.min_free_mb`). They are only kept for minutes anyway (`pi.keep_last_min`).

## The clock set to 1990 (the hand test)

What to expect, with `sudo date -s '1990-01-01 12:00'` **on the Pi** while Desk Talk runs:

- the app log warns once that the clock is before `clock.earliest_believable_date`;
- object-state rows written meanwhile have `clock_synced` 0 (false);
- background jobs keep running on time (see `/api/health`);
- no event-log day is deleted;
- restarting only the app: every object starts over (its saved rows are newer than the clock);
- the Pi's time sync puts the clock right again by itself when it next syncs, if the Pi has internet, and
  the log says the clock looks right again.

`docs/PI_CHECKS.md` has the steps for running it.

## The clock set a year ahead

With `sudo date -s '+1 year'` **on the Pi** while Desk Talk runs: the app log warns once that the clock jumped
ahead, and no event-log day and no object state is deleted. If the Pi has internet, the time server puts the
clock back when it next syncs and the log says the clock looks right again. With no internet, set it back by
hand (`sudo date -s '-1 year'`) and the same happens.

## The clock battery

The Pi 5's clock chip runs from a small battery plugged into J5 when the power is off. Raspberry Pi
sells an official one (the Raspberry Pi RTC Battery): a rechargeable lithium manganese coin cell with the
right two-pin plug and a sticky pad to hold it to the case. It costs a few dollars.

**What it fixes:** after a power cut the clock is right straight away, even with no internet. Saved object
states come back after a power cut, events go under the right day, and the habit day rolls over at the
right time from the first minute.

**What it doesn't fix:** a clock set wrong by hand, or a battery that has run flat. Desk Talk's checks
above stay on either way.

**Setting it up** (all **on the Pi**):

1. Shut the Pi down, plug the battery into J5, and stick it down.
2. Start the Pi and check the clock chip has the time: `timedatectl` shows `RTC time` next to the local
   time, and `System clock synchronized: yes` once it has reached the internet. Linux copies the synced
   time into the clock chip by itself; to do it right away: `sudo hwclock --systohc`.
3. The official battery is rechargeable, but the Pi doesn't charge it unless told to. To turn charging
   on, add this line to `/boot/firmware/config.txt` and reboot:

       dtparam=rtc_bbat_vchg=3000000

   Only do this for a rechargeable cell like the official one. Never charge an ordinary (non-rechargeable)
   lithium coin cell: Raspberry Pi's documentation warns against it.
4. To check the battery later: `cat /sys/devices/platform/soc/soc:rpi_rtc/rtc/rtc0/battery_voltage`.

To check it worked: pull the plug with the Wi-Fi off, wait a few minutes, start the Pi, and run
`timedatectl` before it reaches the internet. The time should be right.

## Recommendation

Buy the official battery and turn on charging for it. It costs a few dollars, takes five minutes to fit,
and removes the most likely way the Pi's clock goes wrong: a power cut while the Wi-Fi is down. Without
it, Desk Talk stays safe (nothing wrong is deleted or picked up) but forgets what was on the desk after
every power cut until the Pi reaches the internet.
