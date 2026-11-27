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

<img width="958" height="548" alt="Screenshot 2026-11-27 at 3 02 17 PM" src="https://github.com/user-attachments/assets/24ba4ee7-f7ab-4969-a59f-b599543a4158" />
smoothing as shown in the Developer Page

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
 
### Roadblocks and challenges

#### Camera speed and picture size

Desk Talk looks at 3 pictures a second, so each picture has to be checked in under 333 ms. My first model (yolo26m-objv1-150 at 640 pixels) managed only 1.7 pictures a second on the Pi 5, so it couldn't keep up.

How I fixed it:

- I wrote a benchmark that tried every model at different picture sizes (640, 480, 320) and formats on both the Mac and the Pi. It recorded the speed, accuracy, memory and temperature for each.
- I made a desk test that scored each model on 240 desk photos plus 6 of my own, so I picked models that are good at desks, not just fast.
- I settled on the smaller yolo26s-objv1-150 at 640 pixels with 1280 × 720 pictures from the camera. It checks every picture in 200–215 ms, uses about 2 of the Pi's 4 cores, and stays around 60 °C.
- I kept pictures in memory instead of saving them to the SD card. That cut card writes from about 500 MB an hour to about 35 MB.


#### Bugs and how I fixed them
- Fake sips. Covering the bottle with a hand, or covering the camera, counted as a sip. Now a sip only counts when the bottle is seen lifted and then put back. If most objects vanish at once, as when the camera is covered, nothing counts.
- Fake objects. "Glasses" kept joining the chat, but I don't wear glasses. Now only objects on a set list can join. A sensitivity slider makes joining stricter or looser, and the developer page shows what the detector thought it saw.
- False safety alerts. A drink behind the laptop looked like it was next to it, and an arm close to the lens looked like a cat. Danger zones now go by where things sit on the desk. A pet only counts when the detector is at least 80% sure in 5 of the last 7 pictures.
- The first model failed on every picture. The detector loaded every model as "detect", but this one was a "segment" model. It now reads the model's type from the file the conversion writes.
- Docker's memory limits did nothing. The Pi started with memory limits turned off. I changed a boot setting, and now a container that uses too much memory gets stopped.
The camera broke after a power cut. The Pi can number the camera's devices differently at each boot. A boot service now writes the current numbers before the containers start.
- A wrong clock after a power cut. The Pi has no clock battery, so it can start days behind. Desk Talk now starts every object over when the clock is behind, and it never deletes anything by date until the clock has been set from the internet.
- A full SD card. I gave Desk Talk its own fixed 8 GB space on the card so it keeps saving even when the rest is full. A 6-hour run with the card nearly full passed.

#### All the tests
- About 7,200 automated tests in Python, plus 32 test files for the developer page. None of them need the network, a camera or the Pi. They use fake versions of each part (a fake clock, a fake detector, a fake AI).
- Every test must pass, and the fast tests must cover at least 97% of the code.
- Separate groups of tests run only when needed: the real YOLO model, the Docker images, the real web page in a browser, and the real Pi over ssh.
- Before each change is merged, the linter, the type checker and a dead-code check all have to come back clean.
- Some things only a person can check, like a power cut, a full SD card or the clock set to 1990. I ran those by hand on the real Pi and wrote down the results.


#### Developer panel

I built a developer page at /dev/ so I could see inside the app while it runs. It has seven tabs:

- Checks: runs all the tests from the browser and shows each one pass or fail.
- Live: shows each object's state and a chart of its smoothing (confidence, the vote and the wait). This is how I found the phantom glasses and the fake sips.
- System: the health of every part of the app.
- Pi: the Pi's processor, memory, temperature, fan, disks and Wi-Fi over the last 30 minutes.
- Pi log: the Pi's logs, live.
- Database and Storage: what's saved and how much room is left.
- Demo: sets off warnings on purpose to show them.

### Limitations

#### Blind spots

Desk Talk only knows what its one camera can see. A water bottle behind my monitor looks, to the system, like a bottle I took away, and anything off the edge of the picture doesn't exist at all.

The camera can't judge depth either. In a flat picture, a cup next to the laptop and a cup a foot behind it look almost the same. I estimate distance from where things sit on the desk, which works most of the time and fails when objects overlap.

The detector also makes mistakes. Early on it kept finding glasses on my desk, and I don't wear glasses. An arm close to the lens can pass for a cat for a moment. The smoothing filters out most of these, though a few still get through. Bad lighting makes it worse, so when the picture is too dark or blurry, Desk Talk reports that the camera is covered.

The model only recognizes about 365 kinds of objects. Desk Talk also ignores anything that lasts less than 3 seconds. I set that wait to filter out flicker, and the side effect is that a very quick sip can go uncounted.

#### Only small models fit on the Pi

The Raspberry Pi 5 has no graphics chip for AI, so the model runs on its 4-core processor. At 3 pictures a second, each picture gets about a third of a second. The model I started with only reached 1.7 pictures a second, so I switched to a smaller one. It keeps up, and it misses more objects than the bigger model did.

The chat AI is too big for the Pi entirely. It runs on my computer through Ollama, or in the cloud, and you can turn it off. Without it, the characters use ready-made lines.

#### The camera can't move

The camera points one way. If someone bumps it, the desk area and danger zones I marked stop lining up until I mark them again. It's also built for one person at one desk, so someone sitting next to me or walking past can confuse it about whether anyone is there.

### Possible extensions

A second camera would cover what's hidden behind the laptop. A depth camera would go further and measure how far away things are.

The Pi has an add-on board for AI, the AI HAT, which should let it run larger models at the same speed. I'd also like to train the model on photos of my own desk. A general model has to recognize hundreds of things, and mine only needs to get about ten of them right.

I'd like to be able to correct it, too. If I could tell it "that wasn't a sip," it could tune its limits to how I actually drink.

My favorite idea is putting the camera on a small motor and letting an AI agent steer it. If my phone went missing, the agent could turn the camera to look for it, and after the camera got knocked it could turn itself back. To do that, it would have to remember where the desk and danger zones are from every angle it can point.

Smaller ideas: a little speaker or screen on the desk so the characters don't need a computer nearby, habits like posture or standing up, and a phone app to replace the web page and notifications.

### Tools and technologies

#### Detection and the camera

- YOLO (Ultralytics) finds the objects in each picture.
- ncnn runs the model fast on the Pi's processor.
- PyTorch and ONNX were used to run and convert the models.
- OpenCV handles the pictures.
- picamera2 reads from the Pi camera.

#### The app

- Python, with FastAPI and Uvicorn as the web server.
- YAML files hold the settings, wording, habit rules and characters, and Pydantic gives each setting a type.
- SQLite saves object states on the Pi.
- Piper is the voice.
- Chat can go through Ollama, Gemini, OpenAI or Groq if you turn it on.
- ntfy sends phone notifications.

#### The Raspberry Pi

- A Raspberry Pi 5 (8 GB) with the camera module.
- Docker Compose runs three containers: the camera, the detector and the app.
- systemd starts them when the Pi boots.

#### The cloud server (optional)

- FastAPI again, with PostgreSQL, SQLAlchemy and Alembic for the database.
- nginx serves the website.
- Claude writes the weekly report.

#### The developer page

- React and TypeScript, built with Vite and tested with Vitest.

#### Testing and code quality

- pytest runs about 7,200 tests, and pytest-cov checks that they cover at least 97% of the code.
- Playwright clicks through the real web pages in a browser.
- Ruff checks style, Pyright checks types, and Vulture finds dead code.
- uv installs packages and runs everything.
- The code lives on GitHub.

Github Link: https://github.com/randomnoobi/desk-talk
