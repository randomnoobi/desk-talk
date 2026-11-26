## Desk Talk is an agentic system that track what is on your desk and turn everyday objects into agents that help you monitor your daily life.

### Inspiration and why I wanted to make the project:

Every day, I sit down at my desk to work, and before I know it, my workspace becomes a mess. I spend time cleaning it up, only for the same thing to happen again the next day. Eventually, I realized that the solution wasn't to clean my desk more often, but to fundamentally change the way I work. Inspired by Andrew Huberman's book *Protocols*, I created DeskTalk, a project designed to help people develop better habits and build a healthier, more productive relationship with their workspace.


### Architecture Digrams

#### The Whole System
<img width="3470" height="1152" alt="DeskTalk-Whole System Diagram" src="https://github.com/user-attachments/assets/73466d1e-46ca-4456-a94b-aa97d7726391" />

Desk Talk runs entirely on a Raspberry Pi 5, split into three Docker containers.

1. Pi Camera → camera. The camera sits above the desk and takes a picture 3 times a second. The camera container is the only one allowed to use the camera.
2. camera → detector. The camera container sends each picture to the detector.
3. detector → camera. The detector runs YOLO26s and sends back a box, a name and a confidence for each object it finds. It handles one picture at a time. If it's still busy, the next picture skips detection, so a queue never builds up.
4. camera → app. The camera container sends the picture and its boxes to the app. The app smooths out flickering detections and keeps track of which objects are on the desk. It turns changes into events like "cup appeared", checks the habit rules (water, breaks, light, phone-free time), and decides what the characters say.
5. app → you. The app talks to you three ways: in the chat and live view on a web page on your Wi-Fi, out loud through a speaker, and, if you turn it on, as notifications on your phone.

#### Picture to Reminder

<img width="5173" height="773" alt="DeskTalk-PictureToReminder" src="https://github.com/user-attachments/assets/25367f8d-ef2b-4875-a174-3f09eb784561" />

1. Observation. The camera takes a picture and the detector finds a cup in it with a confidence score of 0.83.
2. Smoothing. One picture isn't trusted on its own. The cup has to show up across several pictures in a row before Desk Talk counts it as "seen." This stops one bad guess from setting anything off.
3. State. Each object on the desk has a state. Once the smoothed result says the cup is seen, its state changes to PRESENT.
4. Event. That change of state sends out a single event, "cup appeared." The rest of the app reacts to events like this one, not to the raw pictures.
5. Protocol. A habit rule from config/protocols.yaml watches for events. This one checks whether the cup has gone 60 minutes without a sip.
6. Action. When the rule's condition is met, Cup, one of the characters, reminds you to drink.

#### Smoothing

<img width="4732" height="845" alt="DeskTalk-Smoothing" src="https://github.com/user-attachments/assets/c4ab43ad-8ab9-4e0f-90b3-ec12741b6605" />


1. YOLO confidence. For each picture, the detector gives a score for how sure it is that it sees a cup. The scores bounce around from picture to picture: 0.55, 0.48, 0.38, 0.52 ...
2. Hysteresis. A score has to reach 0.5 to switch the cup "on," but it doesn't switch "off" until the score falls below 0.4. Scores in between keep whatever answer was there before. In the example, 0.55 turns the cup on and 0.48 keeps it on. Then 0.38 turns it off, and 0.52 turns it back on. Without this gap, a score hovering around 0.5 would flip the answer on almost every picture.
3. Vote. Desk Talk looks at the last 5 pictures and goes with what most of them say. One odd picture can't change the result.
4. Wait. The voted answer has to stay the same for 3 seconds before Desk Talk believes it. This covers short moments, like a hand passing in front of the cup.
5. Steady state. Only after all of that does the cup's state change to PRESENT or ABSENT. That change is what sends out events like "cup appeared."

#### State Transitions

<img width="3841" height="1041" alt="DeskTalk-StateTransitions" src="https://github.com/user-attachments/assets/36cb0cb0-cb62-4f75-b6e1-d51e4f3fab9d" />

Every object Desk Talk watches, such as a cup, is always in one of three states:

- UNKNOWN: This is where every object starts when Desk Talk turns on, because it hasn't had time to look yet.
- PRESENT: The object is on the desk.
- ABSENT: The object isn't on the desk.

How it moves between them

- UNKNOWN → PRESENT: the object is seen for 3 seconds. This sends an appeared event.
- UNKNOWN → ABSENT: the object is missing for 3 seconds. This sends no event, because nothing actually happened. The object just wasn't there when Desk Talk started. Without this rule, you'd hear "cup removed" every time the app started up.
- PRESENT → ABSENT: the object is missing for 3 seconds. This sends a removed event.
- ABSENT → PRESENT: the object is seen again for 3 seconds. This sends an appeared event.
- PRESENT → PRESENT: if the object stays put but moves more than 20% of the picture's width, its state doesn't change, but it sends a moved event. A small nudge doesn't count.

#### Event Log

<img width="950" height="342" alt="Screenshot 2026-11-26 at 1 06 47 PM" src="https://github.com/user-attachments/assets/173c4035-cb84-4034-bbbb-5b445623b893" />

The fields:
- id, ts. A unique id, and when the event happened, in seconds since 1970 by the Pi's clock.
- type. What kind of event the line is. There are two kinds here:
  - object_state is a change from the state machine. Its fields:
    - change is appeared or removed.
    - object is what the detector saw: mouse, laptop or cell phone.
    - prev_state and new_state are the move, ABSENT → PRESENT or the other way.
    - device_id is which camera saw it, here pi.
    - conf_avg is the detector's average confidence over the pictures since the last change.
    - bbox is where the object sits in the picture, as pixel corners [left, top, right, bottom].
    - dwell_sec is how long the object stayed in its old state before this change.
- agent_joined / agent_left means a character arrived or left the chat. The Phone character joins when a cell phone is on the desk. stayed_sec says how long it stayed, and conf how sure the detector was.

#### Compaction

<img width="3620" height="1104" alt="DeskTalk-Compactiom" src="https://github.com/user-attachments/assets/180fead2-743f-4348-b02d-ce91e37a4ce6" />


How the detection log gets shrunk

- detections.jsonl. While the camera runs, every picture adds one line to this file saying what was seen in it. At several pictures a second, that adds up fast, and most of the lines say the same thing ("cup, cup, cup, ...").
- Compaction. Every 10 minutes (event_log.compact_every_min), Desk Talk goes back over those lines and turns them into time ranges: "cup on desk from start to end." In the diagram, "left" is when a range starts and "right" is when it ends.
  - Gap under 3 seconds: joined. If the cup goes missing for less than 3 seconds (event_log.merge_gap_sec), say because a hand covered it, those pictures stay in the same range. One cup on the desk stays one entry.
  - Gap over 3 seconds: new interval. If it's missing for longer, the first range ends and a new one starts when the cup comes back. So you get two entries: left 1 to right 1 and left 2 to right 2.
