# Fewer false alarms, a clearer trace, Mac notifications and to-do due dates

Date: 2026-10-25. What Sam asked for, and what was decided while talking it through.
"My call" marks a choice the designer made that Sam didn't decide.

## Why

Desk Talk is too easy to fool and too chatty about things that don't matter:

- Covering the water bottle with a hand, or covering the camera, counts as a sip.
- "Glasses" kept joining the chat with no glasses on the desk (Sam doesn't wear any). The detector
  (`yolo26m-objv1-150.pt`, about 150 kinds of things) sometimes sees something that isn't there, and
  nothing stops it: any object the detector finds can join, and the AI invents a personality for it.
- When an object misbehaves there's no way to see why: the developer page shows each object's smoothing,
  but not when it joined the chat, why, or what the camera saw then.

Sam also wants the important messages to reach them when the app page isn't in front (Mac notifications),
a way to make joining the chat stricter or looser without editing files, and to-dos that remind about due
dates.

## What gets built

### 1. Sips: lifted, then put back

Today (`desktalk/core/habits/habit_reader.py`, `HabitReader._sips`): a drink that is gone for
`hydration.sip_gone_min_sec` to `sip_gone_max_sec` (3 s to 2 min) and then comes back is one sip. A hand
over the bottle or a covered camera does the same.

Sam says that when they really drink, the camera still sees the bottle in their hand. So the new rule:

- A sip counts only when the drink is seen **lifted** (its box moves up from where it was resting, by more
  than a share of the picture height, a new setting) and then **put back** (it settles again, in roughly
  the same area, close enough to the desk), with the whole thing taking between the two times we already
  have (3 s to 2 min).
- If the camera loses the drink while it's up in the hand, it still counts, as long as its last sighting
  before vanishing showed it lifted.
- A drink that vanishes and comes back without being seen lifted never counts (a hand over it).
- Extra guard: when most of what's on the desk vanishes at the same moment (a covered camera), nothing in
  that gap counts as a sip. "Most" is a setting.
- The known cost: a tiny, slow sip where the bottle barely rises may be missed. The "how far up" setting
  is how to tune it.
- Buttons and chat (`log_sip` with how='button' / 'chat') are not changed.

### 2. The object's trace on the developer page

On the Live tab (`dev_ui/src/tabs/Live.tsx`), clicking an object opens one panel for it with:

- **A timeline** of the steady state's changes (appeared, removed, moved, from the object states) and the
  chat's joins and leaves (from the roster, which has its own, different waits: the roster is what let
  "glasses" in). Each entry says why: how sure the detector was, how long it had been seen, and whether it
  was inside the marked desk. A join also says which sensitivity step was in use.
- **Its smoothing:** the chart that's there now.
- **Pictures** with its box drawn on: my call: one when it appears, one when it joins the chat, and one
  every so often while it's there (a setting), keeping only the newest handful per kind of object (a
  setting) in a folder under `data/`, so it can't fill the disk. Each picture says when it was taken and
  the box's confidence.

Objects that never get to join the chat (see 4) still show here, so you can see the detector saw them.

### 3. Phantom objects (the glasses)

Fixed by the other pieces together: glasses can no longer join (4), the sensitivity setting can make the
bar stricter (6), and the trace shows what the detector thought it saw, with the picture (2).

### 4. Only objects that matter join the chat

An object can join the chat only when its agent is on a list in `config/settings.yaml` (my call: under
`agents:`). The list, as Sam agreed:

| Agent | Detector words | What it speaks for |
|---|---|---|
| Computer | laptop, keyboard, mouse, tablet, ... | breaks, screens |
| Cup | cup, bottle, mug, water bottle, ... | hydration |
| Phone | cell phone, telephone | phone-free time |
| Snack | the foods | food |
| Book (was Paper) | book, notebook, ... | to-dos |

- Everything else no longer joins: earbuds, headphones, keys, cables, plants, pens, glasses, scissors, the
  power bank, watch, lamp, and any object without a block in `agents.yaml` (no more invented personalities
  for new things).
- Their blocks stay in `config/agents.yaml`, so adding one back is one word in the list.
- **Breaks:** only Computer speaks for them. Take `breaks` off Watch in `agents.yaml`.
- **Light:** Lens speaks for it. Take `light` off Lamp. With no agent for light, `speaker_for` already falls
  back to Lens.
- The detector still sees everything: the danger warnings (a cup next to the laptop), lost items and the
  object states keep working as they do now. Only joining the chat is limited.
- **Watch out:** today the danger warnings (`DeskMonitor.on_detection`, `desktalk/core/vision/desk_monitor.py`)
  only look at `DeskScene.members()`, which the roster fills with who's in the chat. So limiting the chat
  would quietly drop warnings (scissors or a knife near the phone, a remote near the desk edge). My call:
  the warnings get their own "things on the desk" list, built from every detection that's sure enough,
  with fixed waits of their own (the `presence:` numbers), not the chat's waits and not the sensitivity
  slider. That likely means a change to `DeskScene` in `ports.py` (on purpose, with the fakes and contract
  tests). Who speaks a warning: as today when the object is in the chat; when it isn't, the fragile one
  it's near speaks if that one is in the chat, else Lens.

### 5. Mac notifications

Desk Talk shows the Mac's own notification (title: the character's name; text: its message) for:

- **Warnings:** danger alerts, and Lens's camera warnings ("the camera is covered").
- **Reminders:** water, breaks, phone-free time, light, screens, food, to-dos (including the new due-date
  ones), and the rule engine's nudges (the hourly water rule).
- **Sips:** my guess, from Sam's first message ("warnings and sips"): a "Sip logged" notification, with its
  own switch. My call: only sips the camera saw, not ones you logged with a button or in the chat.
- Not: join and leave notes, greetings, small talk, replies to what you typed.

Details:

- It comes from Desk Talk itself (the server), so it shows whether or not an app page is open.
- macOS only. On any other system it does nothing (and says so once in the log).
- My call on how: the Mac's built-in `osascript -e 'display notification ...'`, so nothing new is
  installed. Downside: the notification says it's from "Script Editor", and macOS may ask once to allow it.
  It must never hold up the chat: sent in the background, with a time limit, and a failure is only logged.
- Settings (in `config/settings.yaml`, my call: a new `notifications:` section): one switch for all, one
  per kind (warnings, reminders, sips), and the time limit. Quiet hours (the app's Settings page) mute them
  like they mute reminders.

### 6. Sensitivity

A slider on the app's Settings page (`web_app/`), next to wake time and quiet hours, with five steps from
"Picky" to "Eager". Each step sets, together:

- how sure the detector must be for an object to count toward joining (today `agents.min_confidence`, 0.45)
- how long it must be seen before it joins (today `agents.join_after_sec`, 3 s)

- The middle step is today's numbers. My guess for the ends: "Picky" about 0.65 and 10 s, "Eager" about
  0.35 and 2 s. All five steps are in `config/settings.yaml`.
- The slider shows the two numbers for the step you're on, works right away (no restart), and is saved
  with the other preferences (`PreferencesFile`, `data/preferences.json`).
- My call: it only changes who joins the chat. Sips, warnings and the object states keep their own numbers.
  An object with its own stricter `min_confidence` in `agents.yaml` keeps the stricter one.

### 7. To-dos with due dates

Today a to-do is only text, kept in the browser and sent to the server for the chat
(`desktalk/core/chat/tracker_actions.py`, `web_app/app.js`). Each to-do gets an optional due date:

- **Adding one:** Book's panel gets a date box next to "Add a task" (empty = no due date). In the chat you
  can say it ("add essay, due Friday"): the AI's add tag gets an optional date, which the page fills in.
- **With a due date:** my call: Book says it the day before ("Remember, your essay is due tomorrow"), on
  the day ("Your essay is due today"), and once a day while it's late ("Your essay was due yesterday. Done?
  Tick it off."). A date only, no time of day. Each is said once, while you're at the desk.
- **Without a due date:** the check-in that already exists (`habit_rules.task_nudges`, every
  `tasks.remind_after_min`) stays, but my call: every 2 hours instead of 40 minutes, naming a different
  open to-do each time instead of always the first.
- The panel shows each to-do's due date, and late ones stand out.
- The server must know the due dates, so it can remind with no page open. The to-dos already reach the
  server (`/api/chat_todos`, `/api/chat_todos_sync`); the date goes with them. My call: the server keeps the
  list in `data/` and its copy wins: the page loads it when it opens, and uses its own browser copy only
  when the server has none yet (the first time after this change). So a page with an empty or old list
  can't wipe the server's list, and the server can't bring back to-dos you deleted.
- All wording in `config/prompts.yaml`.

### 8. Small changes

- **Paper becomes Book:** only the name shown (`name:` in `agents.yaml`, and the page's fallback list in
  `web_app/app.js`). Its id stays `paper`, so saved chats and to-dos keep working.
- **No more "+ Glass":** remove it from the water panel and the Habits page in `web_app/app.js`. "+ Sip"
  stays. My call: saying "I drank a glass of water" in the chat still logs a glass (the AI's glass tag in
  `tracker_actions.py`), so `habits.glass_ml` stays.
- **No more "min slouching"** on Computer's panel (`web_app/app.js`, the breaks tracker). The posture checks
  themselves are not changed.

### 9. No more posture or nail biting

Remove all of it: the background check (`VisionHabits`, `desktalk/core/chat/workers/vision_habits.py`), its
interface in `ports.py`, fake and contract tests, its settings, its wording in `prompts.yaml` (the
picture prompt's posture and nail-biting parts, the slouch lines), the "last posture check" the AI is told
(`character_memory.py`'s vision brief), and on the app's Habits page the "nail biting" card and the Camera
log. The starter counters "Nail biting" and "Slouching" go too ("Your own counters" stays, since you add
those yourself). Computer's `about:` line stops saying "posture". The vision model setting stays only if
something else still uses it (lost items do).

### 10. The event log: only what matters to you

The Event log page (`renderLog` in `web_app/app.js`) shows only what you'd care about: sips, breaks, to-dos
ticked off, food, reminders and warnings. Removed from the page (Sam: "the user doesn't need to know these
values"): the camera numbers at the top (pictures a second, waiting to compact, last compaction, pictures
on the Pi), "Compact now", the "On the desk today" timeline, the raw detection printout, joins and leaves,
and the Camera filter. The page asks the server only for what it shows, so it stays quick. Routes nothing
else uses go with it (check `docs/API.md` and the characterization cases); the event log itself
(`data/events/`, compaction) keeps working in the background.

- Which events: an explicit list, never "everything". `habit` events (sips, breaks, food, to-dos ticked
  off, and my call: also sunlight and phone-free streaks, since they're your habits too), `nudge`
  (reminders), `alert` (warnings, with a plain subtitle or none: no raw `feature` words, no "near " with
  nothing after it) and `camera` (Lens's "the camera is covered"). Not: `agent_joined`, `agent_left`,
  `detection`, `compaction`, `object_state`, `rule_outcome`. The server filters, so 150 lines shown are
  150 that matter.
- Filters: Everything, Habits, Reminders, Warnings. The day chips stay, but cover all `event_log.keep_days`
  (not a hardcoded 7), "Today" follows the date (it doesn't stick to yesterday after midnight), and today
  is the server's day, not the browser's.
- Routes that become unused (a reviewer's list; check before removing): `/api/log/presence`,
  `/api/log/detections`, `/api/log/compact`, `/api/habit_log`. `/api/log/status` stays (the developer page
  uses it). Remove their characterization cases, `docs/API.md` rows, settings that only they used, and
  update `tests/vulture_allow.py`.

Ticking off a to-do becomes a logged event (a `HabitEvent` for `tasks`), now that the server keeps the list.

### 11. Other problems found on the app's pages

Three reviewers read the app page (`web_app/`) and its routes. What gets fixed (my calls on which):

**Bugs**
- "Heads up" crashes on any camera problem (`renderRisks`, `app.js` ~1165: a local `lens` hides the
  `lens()` function). If it happens while the page opens, the page never finishes starting. Rename it.
- Polls wipe what you're typing: the tracker panels (to-do, food) and the Habits page are rebuilt every
  15 s, losing half-typed text and focus. Don't rebuild a panel while one of its inputs has focus or
  text, and skip the rebuild when nothing changed.
- Messages sent while the page was cut off never show up: fetch the recent messages again when the
  connection comes back (the live chat stream reconnects, or the "can't reach" note goes away).
- A server that's slow to start leaves the page in demo mode for good: keep trying in the background
  and switch to live when it answers.
- The QR code is fetched again every other poll (the reply's `qr_svg: null` overwrites it): keep the one
  we have.
- Settings: adding or removing a danger zone resets wake time, bedtime and quiet hours you typed but
  didn't save yet. Keep them. Save messages say which part failed, check the hours in the page (no more
  25 → 1), and "Saved" goes away after a while. "Warn at" can't be set closer than "Alert at".
- "Clear all chats" asks first, and cleared messages don't come back after a refresh (remember when you
  cleared).
- Captures: a failed load says "Couldn't load photos" (not "No photos yet") with a retry, and the page
  refreshes while open. The intro says honestly that photos are kept until Desk Talk restarts. Times
  show the date, not just the weekday.
- A failed load anywhere on the Habits and Event log pages says so instead of showing empty or old numbers.
- Your own counters notice a new day (not only on reload); the hint says they reset at midnight.
- Sunlight buttons say what they mean ("Clear sky, 10 min" ...), and afternoon light shows on the card.
- "near-spill alerts" becomes "desk warnings" (it counts every warning).
- The typing dots stay until that reply arrives, not until any message arrives.
- The "Camera connected / disconnected" note only shows after the change has lasted a few seconds (a
  setting), so a flaky camera doesn't spam it.

**Plain words** (the user doesn't need these values): no px, lux, "120/255", percentages, "Detected as
cup", raw detector words ("dining table", "pen/pencil"), "Protocols: ...", the AI's "evidence" lines,
`python server.py`, `.env` names or `config/settings.yaml` on the app page. Each tracker panel gets one short
plain line; the sip line describes the new lifted-and-put-back rule. "Heads up" says plain sentences (no
"34px", "12% closer in 5 frames"). Quiet hours use time pickers. The Raspberry Pi command stays (it's
needed) but only in one place.

**Speed**
- `GET /api/live` (every 1.5 s) only sends what the app page reads. Anything only the developer page needs
  moves behind `/api/dev` (check `dev_ui/src/api.ts` first).
- Polling pauses while the tab is hidden. The live video only loads while the camera panel is open.
- The side panel's photos ask for just the newest few (a `limit` on `/api/snapshots`) and only refresh
  when a new photo message arrives or on a slow timer.
- Lists only rewrite when they changed (the on-desk list, the chat list).

**Leftovers and rules**
- The light tracker panel can't be reached once Lamp can't join: remove it (light stays on the Habits page).
- The hardcoded counts in `app.js` (how many objects, issues, risk lines, days, quote and toast lengths,
  mention length, counter name length) come from `GET /api/ui_settings`; the starter to-do and counter
  names come from `prompts.yaml`.

**Accessibility**: clickable photos become buttons; links without `href` become buttons; each progress bar
and counter button says what it's for; settings inputs get real labels; the Desk cam button gets
`aria-expanded`.

### Settled after the review

- To-do reminders with no book on the desk: Lens says them, as today.
- Quiet hours mute every notification, warnings included.

## Not doing

- Not changing the detector, its model or its numbers.
- Not limiting what the detector sees, the danger warnings, lost items or the object states. Only the chat.
- No notifications on Windows or Linux.
- No times of day on due dates, no repeating to-dos.
- The sensitivity slider doesn't change sips or warnings.
- Not moving the habit day's start from midnight to wake time (a reviewer noticed that after midnight the
  water bar looks far behind until bedtime). It's a bigger change for another time.
- Not changing how saved danger zones stop following `settings.yaml` after the first save.
- The desk-corner picture stays mouse-only for now.

## Rules that apply (from AGENTS.md)

- Every number, timing and threshold goes in `config/settings.yaml` (or `protocols.yaml` for habit rules),
  with a plain comment and a typed field in `desktalk/config/app.py`. Wording goes in `config/prompts.yaml`.
- New parts talk through `desktalk/ports.py`, are built in `desktalk/bootstrap.py`, and get a fake in
  `tests/fakes/` and a contract suite.
- New routes get a characterization case. Tests first. Coverage may only go up.
- The web pages keep no copies of settings: `web_app/` gets new numbers from `GET /api/ui_settings`,
  `dev_ui/` from `GET /api/dev/settings`.

## Left to the builder

- How "lifted" and "put back" are measured exactly, and the settings' starting values.
- Which numbers the picture samples use, and the folder name.
- How the Mac notification is run in the background and how it finds out it's on a Mac.
- The look of the slider, the date box and the trace panel, in the style the pages already have.
