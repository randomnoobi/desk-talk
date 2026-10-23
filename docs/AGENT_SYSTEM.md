# Agent System — who's in the chat, and how they talk

## Agents come and go with the objects

Nobody is in the group chat when Desk Talk starts. The **roster**
(`Roster`, `desktalk/core/agents/roster.py`) listens to the detection events on the event stream:

- An object seen for `agents.join_after_sec` (3 s) → its agent joins: **"Cup joined the chat"**,
  then says hello (`hello:` in `config/agents.yaml`).
- Missing for `agents.leave_after_sec` (45 s) → **"Cup left the chat"**. Its 1-on-1 chat stays in
  the sidebar (greyed out) so you can still read it.
- `person`, furniture and the desk itself never join (`ignore:` in `config/agents.yaml`).
- Once desk corners are marked (Settings → Desk edges), only things on the desk can join.
- If the camera stops, nobody leaves: no pictures means no events, and Lens says the camera is off.

Short sightings don't count: an object has to stay in view for the whole join
time, and a few missed pictures don't make it leave (`PresenceTracker`,
`desktalk/core/agents/presence.py`). The roster puts `AgentJoined` and `AgentLeft`
events back on the event stream; the message generator turns them into the
join and leave notes, and the event log keeps them.

It makes no difference where the objects were found: on the Pi (its detector
container) or on your computer, the detection events look the same.

## Profiles (`config/agents.yaml`)

A profile gives an object its name, look and personality. Several detector words
can share one agent: `laptop`, `keyboard`, `mouse` and `tv` are all **Computer**.
`ProfileBook` (`desktalk/core/agents/profiles.py`) reads the file and answers
"which agent is this detector word?" (`for_label`).

| Agent | Detector words (some) | Speaks for | Tracker |
|---|---|---|---|
| Computer (Monty) | laptop, keyboard, mouse, tv | breaks, screens | breaks |
| Cup (Glug) | cup, bottle, mug, wine glass | hydration | hydration |
| Snack (Munch) | banana, apple, pizza, chips… | food | food |
| Paper (Sheets) | book, notepaper, folder | tasks | to-do list |
| Phone (Buzz) | cell phone | phone-free time | phone-free |
| Lamp | lamp | light | light |
| AirPods, Headphones, Keys, Cable (Zip), Sprout, Pen, Watch, Glasses, Scissors, Power Bank (Surge) | … | | |

**Anything else** (a stapler, a candle…) still joins, using the `default` block. If
an AI is connected, it invents a one-line personality the first time
(`agents.invent_personality`, at most `agents.personality_max_chars` letters), saved in
`data/learned_personalities.json`.

**Lens** is always there: it's the camera (`Lens`, `desktalk/core/chat/workers/lens.py`).
It speaks about the camera view (dark, blurry, the Pi stopped sending), about the Pi
itself (running hot, its clock off, its own detector off), and says habit reminders
when no agent that speaks for that habit is on the desk. Lens never uses the AI.

## When agents talk

| Trigger | Who speaks | Code |
|---|---|---|
| Joins the chat | that agent (hello) | `Roster` → `AgentJoined` → `MessageGenerator.on_roster` |
| Something risky enters a danger zone | the fragile one (Computer, Paper, Phone) or the intruder | `DeskMonitor` → `Alert` → `MessageGenerator.on_alert` |
| Near a marked desk edge | that agent | same |
| Sip, break, food spotted, phone-free streak | an agent for that habit, or Lens | `HabitTracker` → `HabitEvent` → `MessageGenerator.on_habit` |
| A Protocols reminder is due | an agent for that habit, or Lens | `HabitReminders` (words from `MessageGenerator.phrase_nudge`) |
| You write in the group | @mentioned agents, the one you name, or a random one on the desk | `POST /api/chat` → `MessageGenerator.answer` |
| You write 1-on-1 | that agent | `POST /api/chat` → `MessageGenerator.answer` |
| "Have you seen my keys?" | Paper if on the desk, else Lens | `LostItems.answer` |
| A tracked item has been gone a while | Lens | `LostItems` → `Alert` → `MessageGenerator.on_alert` |
| Every 12–25 min, 2+ agents on the desk | a small-talk round | `IdleChat` |
| 9 pm | a random agent: the daily recap | `DailySummary` |

Every message goes out through the **message hub** (`MessageHub`,
`desktalk/core/chat/message_hub.py`) to every open app page, over Server-Sent Events
(`GET /api/events`). The hub also keeps the recent messages for a page that opens later
(`GET /api/messages/recent`).

`HabitReminders`, `Lens`, `IdleChat`, `LostItems`, `DailySummary` and `VisionHabits` are
background jobs (`PeriodicWorker`s) run by the Supervisor: if one round fails (say, the AI
returns something broken), it's logged and the job carries on a little later, instead of
going quiet until the next restart. The background chat jobs are in
`desktalk/core/chat/workers/`; `HabitReminders` is in `desktalk/core/habits/reminders.py`.

## When the AI is down

The parts never call the AI directly: they get a `ChatModel`, which is the real
connection (`OpenAICompatibleChatModel`) wrapped in `ResilientChatModel`
(`desktalk/adapters/llm/`). It adds a time limit, retries, a breaker that leaves the AI
alone for a while after repeated failures, and a rest for background chatter after a
rate limit. When the AI still can't answer, the characters speak anyway: an alert uses
the agent's own `too_close:` line, a reminder the nudge's ready-made text, and your own
message gets a plain reason instead of a reply.

## Prompts

Every prompt is in `desktalk/core/chat/prompts.py`. A chat reply is built in three layers:

1. **Identity + rules**: name, object, personality, habit focus, action tags.
2. **Live desk status** (`desktalk/core/chat/context.py`): who is in the chat, what else the camera sees,
   active dangers, today's habit numbers, the tracker's data, memory of earlier replies.
3. **The conversation**: recent turns word for word, older ones summarized.

Replies are cleaned so an agent never speaks for another one, and dramatic claims
the camera didn't see ("the laptop is about to shatter") are dropped.

## Trackers and action tags

The `tracker:` field puts a panel on top of the agent's 1-on-1 chat. The AI can
change it with tags in its reply: `[LOG_SIP]`, `[LOG_GLASS]`, `[LOG_BREAK]`,
`[LOG_SNACK_HEALTHY:almonds]`, `[LOG_SUNLIGHT:15]`, `[ADD_TODO:…]`,
`[DONE_TODO:…]`, `[DEL_TODO:…]`. Habit tags are applied on the server (in the habit
tracker); to-do tags in the browser. See `desktalk/core/chat/tracker_actions.py`.
