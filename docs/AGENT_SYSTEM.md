# Agent System — who's in the chat, and how they talk

## Agents come and go with the objects

Nobody is in the group chat when Desk Talk starts. The **roster**
(`desktalk/agents/roster.py`) listens to the detection events:

- An object seen for `agents.join_after_sec` (3 s) → its agent joins: **"Cup joined the chat"**,
  then says hello (`hello:` in `config/agents.yaml`).
- Missing for `agents.leave_after_sec` (45 s) → **"Cup left the chat"**. Its 1-on-1 chat stays in
  the sidebar (greyed out) so you can still read it.
- `person`, furniture and the desk itself never join (`ignore:` in `config/agents.yaml`).
- Once desk corners are marked (Settings → Desk edges), only things on the desk can join.
- If the camera stops, nobody leaves: no pictures means no events, and Lens says the camera is off.

Short sightings don't count: an object has to stay in view for the whole join
time, and a few missed pictures don't make it leave.

## Profiles (`config/agents.yaml`)

A profile gives an object its name, look and personality. Several detector words
can share one agent: `laptop`, `keyboard`, `mouse` and `tv` are all **Computer**.

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
(`agents.invent_personality`), saved in `data/learned_personalities.json`.

**Lens** is always there: it's the camera. It speaks about the camera view
(dark, blurry, the Pi stopped sending), and says habit reminders when no agent
that speaks for that habit is on the desk. Lens never uses the AI.

## When agents talk

| Trigger | Who speaks | Code |
|---|---|---|
| Joins the chat | that agent (hello) | `MessageGenerator.on_roster` |
| Something risky enters a danger zone | the fragile one (Computer, Paper, Phone) or the intruder | `DeskMonitor` → `alert` → `MessageGenerator.on_alert` |
| Near a marked desk edge | that agent | same |
| Sip, break, food spotted, phone-free streak | an agent for that habit, or Lens | `HabitTracker` → `habit` → `on_habit` |
| A Protocols reminder is due | an agent for that habit, or Lens | `HabitReminderThread` |
| You write in the group | @mentioned agents, the one you name, or a random one on the desk | `POST /api/chat` |
| You write 1-on-1 | that agent | `POST /api/chat` |
| "Have you seen my keys?" | Paper if on the desk, else Lens | `LostItems.answer` |
| Every 12–25 min, 2+ agents on the desk | a small-talk round | `IdleChatThread` |
| 9 pm | a random agent: the daily recap | `DailySummaryThread` |

## Prompts

Every prompt is in `desktalk/chat/prompts.py`. A chat reply is built in three layers:

1. **Identity + rules**: name, object, personality, habit focus, action tags.
2. **Live desk status** (`chat/context.py`): who is in the chat, what else the camera sees,
   active dangers, today's habit numbers, the tracker's data, memory of earlier replies.
3. **The conversation**: recent turns word for word, older ones summarized.

Replies are cleaned so an agent never speaks for another one, and dramatic claims
the camera didn't see ("the laptop is about to shatter") are dropped.

## Trackers and action tags

The `tracker:` field puts a panel on top of the agent's 1-on-1 chat. The AI can
change it with tags in its reply: `[LOG_SIP]`, `[LOG_GLASS]`, `[LOG_BREAK]`,
`[LOG_SNACK_HEALTHY:almonds]`, `[LOG_SUNLIGHT:15]`, `[ADD_TODO:…]`,
`[DONE_TODO:…]`, `[DEL_TODO:…]`. Habit tags are applied on the server; to-do tags
in the browser. See `desktalk/chat/widgets.py`.
