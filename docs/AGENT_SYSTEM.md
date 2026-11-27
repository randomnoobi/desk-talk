# Agent System: who's in the chat, and how they talk

## Agents come and go with the objects

Nobody is in the group chat when Desk Talk starts. The roster
(Roster, desktalk/core/agents/roster.py) listens to the detection events on the event stream:

- When an object has been seen for agents.join_after_sec (3 s), its agent joins ("Cup joined the chat")
  and then says hello (hello: in config/agents.yaml).
- When it has been missing for agents.leave_after_sec (45 s), the chat shows "Cup left the chat". Its
  1-on-1 chat stays in the sidebar, greyed out, so you can still read it.
- person, furniture and the desk itself never join (ignore: in config/agents.yaml).
- Once desk corners are marked (Settings → Desk edges), only things on the desk can join.
- If the camera stops, nobody leaves: no pictures means no events, and Lens says the camera is off.

Short sightings don't count. An object has to stay in view for the whole join
time, and a few missed pictures don't make it leave (PresenceTracker,
desktalk/core/agents/presence.py). The roster puts AgentJoined and AgentLeft
events back on the event stream; the message generator turns them into the
join and leave notes, and the event log keeps them.

The detection events look the same whether the objects were found on the Pi
(by its detector container) or on your computer.

## Profiles (config/agents.yaml)

A profile gives an object its name, look and personality. Several detector words
can share one agent: laptop, keyboard, mouse and tv are all Computer.
ProfileBook (desktalk/core/agents/profiles.py) reads the file and answers
"which agent is this detector word?" (for_label).

- Computer (Monty): Detector words (some): laptop, keyboard, mouse, tv. Speaks for: breaks, screens. Tracker: breaks
- Cup (Glug): Detector words (some): cup, bottle, mug, wine glass. Speaks for: hydration. Tracker: hydration
- Snack (Munch): Detector words (some): banana, apple, pizza, chips…. Speaks for: food. Tracker: food
- Book (Sheets): Detector words (some): book, notepaper, folder. Speaks for: tasks. Tracker: to-do list
- Phone (Buzz): Detector words (some): cell phone. Speaks for: phone-free time. Tracker: phone-free
- Lamp: Detector words (some): lamp. Speaks for: light. Tracker: light
- AirPods, Headphones, Keys, Cable (Zip), Sprout, Pen, Watch, Glasses, Scissors, Power Bank (Surge): Detector words
  (some): …

Only Computer, Cup, Phone, Snack and Book join the chat (agents.can_join in config/settings.yaml),
and only once the camera has been sure enough of one for long enough. The Sensitivity slider in the app's
Settings picks the step (agents.sensitivity_steps). Everything else (glasses, scissors, keys, a stapler…)
is turned away, and the developer page's trace says so. Those things still count for the desk warnings: the
roster keeps a second list of everything on the desk (DeskScene.on_desk()), and the warnings read that one.

Lens is the camera (Lens, desktalk/core/chat/workers/lens.py), and it is always in the chat.
It speaks about the camera view (dark, blurry, the Pi stopped sending) and about the Pi
itself (running hot, its clock off, its own detector off). It says habit reminders
when no agent that speaks for that habit is on the desk, and gives the desk warnings nobody in the chat can
give ("Heads up: scissors next to the cell phone"). Lens never uses the AI.

## When agents talk

- Joins the chat: That agent (hello). Code: Roster → AgentJoined → MessageGenerator.on_roster
- Something risky enters a danger zone: The fragile one (Computer, Book, Phone) or the intruder, if in the chat; else
  Lens. Code: DeskMonitor → Alert → MessageGenerator.on_alert
- Near a marked desk edge: That agent if in the chat, else Lens. Code: same
- Sip, break, food spotted, phone-free streak: An agent for that habit, or Lens. Code: HabitTracker → HabitEvent →
  MessageGenerator.on_habit
- A Protocols reminder is due: An agent for that habit, or Lens. Code: HabitReminders (words from
  MessageGenerator.phrase_nudge)
- You write in the group: @mentioned agents, the one you name, or a random one on the desk. Code: POST /api/chat →
  MessageGenerator.answer
- You write 1-on-1: That agent. Code: POST /api/chat → MessageGenerator.answer
- "Have you seen my keys?": Book if on the desk, else Lens. Code: LostItems.answer
- A tracked item has been gone a while: Lens. Code: LostItems → Alert → MessageGenerator.on_alert
- Every 12 to 25 min, 2+ agents on the desk: A small-talk round. Code: IdleChat
- 9 pm: A random agent: the daily recap. Code: DailySummary

Every message goes out through the message hub (MessageHub,
desktalk/core/chat/message_hub.py) to every open app page, over Server-Sent Events
(GET /api/events). The hub also keeps the recent messages for a page that opens later
(GET /api/messages/recent). Warnings, habit reminders and the sips the camera saw first pass through
DesktopAlerts, which also shows them as Mac notifications (the notifications section of the settings).

HabitReminders, Lens, IdleChat, LostItems and DailySummary are
background jobs (PeriodicWorkers) run by the Supervisor. If one round fails (say, the AI
returns something broken), the error is logged and the job tries again a little later, so it
doesn't go quiet until the next restart. The background chat jobs are in
desktalk/core/chat/workers/; HabitReminders is in desktalk/core/habits/reminders.py.

## When the AI is down

The parts never call the AI directly. They get a ChatModel, which is the real
connection (OpenAICompatibleChatModel) wrapped in ResilientChatModel
(desktalk/adapters/llm/). The wrapper adds a time limit and retries. After repeated failures a breaker
leaves the AI alone for a while, and after a rate limit the background chatter rests. When the AI
still can't answer, the characters speak anyway: an alert uses the agent's own too_close: line, a
reminder uses the nudge's ready-made text, and your own message gets a plain reason instead of a reply.

## Prompts

Every prompt, and every fixed line (Lens's wording, the habit nudges, the replies when the AI
is down), is in config/prompts.yaml. The code only fills in the blanks
(desktalk/core/chat/prompts.py knows which blanks each prompt has). A chat reply
(desktalk/core/chat/chat_answers.py) is built in three layers:

1. Identity and rules: name, object, personality, habit focus, action tags.
2. Live desk status (desktalk/core/chat/live_context.py): who is in the chat, what else the camera sees,
   active dangers, the latest alerts, today's habit numbers, the tracker's data, and how the objects you
   @mention are doing. What the agent said lately comes from its memory (desktalk/core/chat/memory.py).
3. The conversation (desktalk/core/chat/history.py): recent turns word for word, older ones summarized.

desktalk/core/chat/names.py works out who answers in the group (@mentions, or an agent named in plain
text). desktalk/core/chat/replies.py cleans the replies so an agent never speaks for another one, and
drops dramatic claims the camera didn't see ("the laptop is about to shatter").

The rest of the app talks to MessageGenerator (desktalk/core/chat/message_generator.py).
It builds the pieces that do the work: alert_voice.py (alerts in an agent's voice), nudge_voice.py
(habit reminders in an agent's voice), chat_answers.py (answering you) and memory.py (the only part
that writes what the characters remember, into CharacterMemory).

## Trackers and action tags

The tracker: field puts a panel on top of the agent's 1-on-1 chat. The AI can
change it with tags in its reply: [LOG_SIP], [LOG_GLASS], [LOG_BREAK],
[LOG_SNACK_HEALTHY:almonds], [LOG_SUNLIGHT:15], [ADD_TODO:…],
[DONE_TODO:…], [DEL_TODO:…]. The server applies habit tags (in the habit
tracker), and the browser applies to-do tags. See desktalk/core/chat/tracker_actions.py.
