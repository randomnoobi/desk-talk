# Desk Talk

**A habit-tracking app that brings your desk objects to life.**

A camera watches your desk. Every object it recognizes joins a group chat as a
character ("Cup joined the chat"), and leaves when you take it away. The
characters look out for each other (Computer panics when the cup gets close) and
for you: water, movement breaks, sunlight and dim evenings, and phone-free
focus time, following Andrew Huberman's *Protocols*.

---

## Quickstart

You need:

- a Raspberry Pi with the camera module, running 64-bit Raspberry Pi OS, on your Wi-Fi. Only a Pi 5 with 8 GB has
  been tried, and the install stops on a Pi with under about 2 GB of memory;
- a 32 GB SD card or larger. The first install needs about 14 GB free, so a 16 GB card is too small;
- a USB speaker, if you want to hear warnings and reminders. Nothing else needs it.

**On the Pi**, run:

```bash
git clone https://github.com/randomnoobi/desk-talk ~/desk-talk && bash ~/desk-talk/raspberry_pi/install.sh
```

The script checks the Pi, installs Docker if it's missing, fetches the model and starts the three containers, which
start again by themselves when the Pi boots. The first run builds the images, which took about 9 minutes on a Pi 5. You can run it again safely. At the end it prints
the address to open, usually **http://desktalk.local:5000**, on any phone or computer on the same Wi-Fi.

To get new code later, **on the Pi**:

```bash
bash ~/desk-talk/raspberry_pi/update.sh
```

If something goes wrong, see the Pi guide, [raspberry_pi/README.md](raspberry_pi/README.md). The install has not been
run on a blank SD card yet. What still needs a person to try on a real Pi is in
[docs/RUNBOOK.md](docs/RUNBOOK.md#checking-on-the-real-pi).

No Pi? Serve `web_app/` with any static server, or open the GitHub Pages site, and the page plays a short demo. To
work on the code on a computer, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## How it works

Everything runs on the Raspberry Pi, in three Docker containers from one compose file. You don't need a computer, an
AI or an account. Phones and computers on your Wi-Fi only open the app's page.

- `camera` (`raspberry_pi/camera/`) takes the pictures, keeps them on the Pi and deletes them every 10 minutes.
- `detector` (`raspberry_pi/detector_service/`) runs YOLO. If it is loading or has crashed, the live view still
  works, Lens says the detector is off, and Docker starts it again.
- `app` (`desktalk/`) turns each picture into events, runs the characters and the habits, and serves the page.

Pictures never leave the Pi, except to a page someone opens on your own Wi-Fi. There is no login on the Wi-Fi, so
anyone who can join it can open the page. More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Optional extras

- Chat with the characters, through an AI on your own computer (Ollama) or your cloud server:
  [docs/AI_AND_CLOUD.md](docs/AI_AND_CLOUD.md). Warnings, reminders and habits work without it.
- Warnings and reminders on your phone, through ntfy: [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md#phone-notifications).
- A cloud server you run yourself, which keeps your habit events and writes a weekly report:
  [cloud/README.md](cloud/README.md).

## Docs

| Page | What's in it |
|---|---|
| [CHARACTERS.md](docs/CHARACTERS.md) | who joins the chat and when, their personalities, Lens, and how they get along |
| [HABITS.md](docs/HABITS.md) | the habits from *Protocols* and what the desk does for each, and to-dos |
| [APP.md](docs/APP.md) | the app page, the event log, desk edges, the demo and the design principles |
| [NOTIFICATIONS.md](docs/NOTIFICATIONS.md) | the speaker and phone notifications |
| [AI_AND_CLOUD.md](docs/AI_AND_CLOUD.md) | adding chat AI, and linking a cloud server |
| [DETECTION.md](docs/DETECTION.md) | the YOLO model, where it runs and how fast |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | every settings file and what's in it |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | running it on a computer, the tests, the developer page and the folder layout |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | how the parts fit together and what happens when one fails |
| [raspberry_pi/README.md](raspberry_pi/README.md) | the full Pi guide: install, update, backups, logs |
| [RUNBOOK.md](docs/RUNBOOK.md) | when the Pi needs a person, and the by-hand checks on a real Pi |

## License

MIT. See [LICENSE](LICENSE).
