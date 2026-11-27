# The app page

Open http://desktalk.local:5000 on any phone or computer on the same Wi-Fi as the Pi. The page is one window with three
columns:

1. Chats, on the left: "The desk" group chat, then everyone on the desk (green dot) and anyone who left earlier.
   Habits, Event log, Captures and Settings sit underneath.
2. The conversation, in the middle: join and leave notes, colored bubbles, proof photos and @mentions. Each 1-on-1
   chat has its tracker on top (Cup's water, Computer's breaks, Book's to-do list ...).
3. The desk cam, on the right: the live picture with boxes, who's on the desk, current risks and proof photos. Until
   the camera connects, it says so.

## An object's story

Click anything under "On the desk" for its story: when it appeared, joined the chat, moved or went away, how sure the
camera was each time, and the picture taken at each step with its box drawn on (for "went away", the empty spot with
its last box). The pictures are kept in `data/trace_photos/` (set in the `object_story` settings).

## Event log

What happened each day, newest first: sips, breaks, food, finished to-dos, sunlight, reminders, warnings and camera
problems, with filters for habits, reminders and warnings.

## Desk edges

In Settings, tap the corners of your desk on the camera picture. Then only things on the desk can join the chat, and
things near a real edge get a warning.

## The demo

With no server to talk to, the page plays a short demo where four characters join the chat. Serve `web_app/` with any
static server, or open the GitHub Pages site.

Pushing to `main` publishes `web_app/` to GitHub Pages ([`.github/workflows/deploy-pages.yml`](../.github/workflows/deploy-pages.yml)).
To set it up for your own copy of the repository:

1. On GitHub, open Settings, then Pages, and under Build and deployment set Source to GitHub Actions.
2. Push to `main`, open the Actions tab and check that "Deploy site to GitHub Pages" succeeds.
3. The site is at `https://<username>.github.io/<repo>/`, unless you use a custom domain.

## Design principles

1. Familiar over novel. It's a chat app, and everyone knows how to use one.
2. Personality through type. Each character is known by its bubble shape, color and font.
3. Built on relationships. What the characters say comes from how they get along ([CHARACTERS.md](CHARACTERS.md)).
4. Gentle. The app nudges; it doesn't nag.
5. The camera stays out of sight. You talk to characters, not to boxes and scores.
6. Local first. Everything runs on your desk, on your Pi.
