# AI usage log

The Congressional App Challenge requires disclosing all AI use. This file lists every change made with an AI assistant (Claude, by Anthropic) so it can be copied into the submission. Everything not listed here was in the project before.

**Still Sam's original work:** YOLO detection and the camera loop, the relationship and danger-zone engine (`backend/relationships.py`), the character personalities and LLM prompts, habit tracking, lost-object tracking, the idle-chat director, the daily recap, and the website's text and scene drawings.

**See for yourself:** [Sam's code before any AI help](https://github.com/randomnoobi/desk-talk/tree/original-by-sam) · [every AI-assisted change since](https://github.com/randomnoobi/desk-talk/compare/original-by-sam...main)

---

## Oct 8, 2026: phone camera over Wi-Fi

**Goal:** use the phone's camera instead of a USB webcam. The phone streams the desk to the computer, which runs detection and shows the chat.

| File | Change |
|---|---|
| `backend/phone_camera.py` (new) | `PhoneFrameSource` holds the newest phone frame and has a `read()` like `cv2.VideoCapture`, so the existing YOLO loop works unchanged. Also makes the HTTPS certificate, pairing key, and QR code. |
| `phone/` (new) | Page that runs on the phone: opens the camera, draws each frame to a canvas, and POSTs it as a JPEG about 10 times a second. |
| `backend/server.py` | `CAMERA_SOURCE` setting (phone by default). `CameraThread` accepts a `source`. A second small Flask app (`phone_app`) on HTTPS port 5443 only serves the phone page and accepts frames. Analysis pauses when the phone disconnects. |

## Oct 10, 2026: new art, simpler interface, faster streaming

**Art (`app/art/`, new).** Original characters drawn as SVG in a flat cut-paper style with a gouache-like grain (an SVG noise filter): Computer (laptop), Cup (mug), Snack (pizza slice), Paper (sheet), the group icon, a desk scene, and small doodles. Sam chose the style from a reference image; no part of that image was copied.

**Font (`app/fonts/`, new).** Bricolage Grotesque, saved into the project so the app works offline (SIL Open Font License, included as `OFL.txt`).

**Computer interface (`app/index.html`, `app/style.css`, `app/app.js`), rewritten.** The phone-shaped mockup became a desktop layout: chats on the left, the conversation in the middle, the desk cam on the right.
- Kept: group chat and 1-on-1 chats, @mentions, proof photos, the four trackers (to-do list, breaks, sips, snacks), habit log, captures, and all settings (danger zones, desk edge, quiet hours).
- Removed to simplify: the splash screen, fake phone frame, bottom tab bar, floating "danmu" comments over the video, swipe gestures, and the Live/Demo switch. The app now detects the server on its own.
- New: a demo mode with a short scripted conversation when no server is running (for the GitHub Pages site).
- About 4,000 lines became about 1,600.

**Phone page (`phone/`).** Restyled to match.

**Website (`website/style.css`).** Uses the new font and colors. The background photo was replaced with the desk scene illustration, because the old photo was a watermarked stock image.

**Speed (`backend/server.py`, `backend/phone_camera.py`).**
- The video stream only sends a frame when there's a new one. Before, it re-sent the same frame every 30 ms (about 33 per second per viewer).
- The phone's JPEG is reused instead of being decoded and re-encoded.
- New `/api/live` route: the desk cam panel makes one request every 1.5 s instead of three or four.
- Fixed the "have you seen my keys?" reply text to point at the new Captures page.

**Install fix (`requirements.txt`).** Removed `numpy<2` and `opencv-python<4.12`. Those pins can't install on Python 3.13 or newer (what an updated Mac has). Tested on Python 3.13 with numpy 2, OpenCV 5, and the latest YOLO.

## Oct 10, 2026 (later): fixes after testing on a real desk

Sam tested the app and reported five problems. Each is listed with what caused it and what changed.

**1. The video turned on its side when the phone was horizontal.**
Cause: with the screen rotation lock on, the phone keeps sending the picture in portrait, even when it's held sideways.
Fix (`phone/phone.js`): the phone page reads the motion sensor to tell which way is really up and turns each frame before sending it. A small "Computer sees" preview shows exactly what the computer gets, and a Turn button adds a quarter turn by hand if a phone gets it wrong. On the computer, the video box now takes the picture's shape instead of a fixed 4:3 box.

**2. Things near the edge of the picture were called "near the edge of the desk".**
Cause: `backend/relationships.py` measured distance to the border of the camera picture, not the desk.
Fix: in Settings you tap the corners of the desk on a camera picture. Only those lines count as desk edges (sides that run along the border of the picture are ignored, because the desk keeps going there). It measures from the bottom of each object, where it touches the desk, because the camera looks at the desk at an angle. With no desk marked, there are no edge warnings. Sam's danger levels and sliders are unchanged.

**3. The characters stopped answering.**
Cause: the free AI plan's limit was being used up in the background. Idle chatter ran every 30 seconds, and reminders, vision checks, and summaries all made AI calls too (27 calls in 3 minutes in a test). Once the limit was hit, chat requests failed with no message, so it looked like nobody was listening.
Fix (`backend/server.py`): background features now run much less often (idle chat every 12 to 25 minutes, only when 2+ objects are on the desk and someone is there), and they pause for 10 minutes after a rate-limit error so your own messages still get through. When a reply fails, the chat shows why in plain words ("hit its free usage limit", "key was rejected", and so on), and a line above the message box says so until the AI works again. Group replies are also sent back with the request, so they show up even if the live connection drops. Chats are now saved in the browser so a refresh doesn't clear them.

**4. Reminders came with no reason, even right after opening the app.**
Cause: reminders were on timers that started when the server started, and a few "time-based" nudges (posture, screen time) never looked at the camera.
Fix (`backend/reminders.py`, new): each character only reminds you when the camera saw a real reason, and the message says it: Computer after 50 minutes with no break, Cup after 45 minutes with no sip, Snack when food has sat on the desk for 20 minutes, Paper when there are open to-dos and 40 minutes have passed. Nothing is sent for the first 15 minutes of a session, while the phone camera is off, during quiet hours, or when nobody is at the desk. The timer-only posture and screen-time nudges were removed. Vision nudges (only with Ollama) now need to see the same thing twice in a row and wait 20+ minutes before repeating.

**5. Nobody told you when the camera itself had a problem.**
Fix: a new character, Lens (the phone camera), drawn in the same style (`app/art/lens.svg`). `backend/camera_health.py` (new) checks the video about once a second and Lens says something in the group chat when the view is too dark, has glare, is blurry, is covered, shows nothing it recognizes for 3 minutes, cuts off an object at the side of the picture, when the phone was bumped (the tilt changed since the desk was marked), when the video is slow, or when it stopped (unless you pressed Stop). It says thanks once the problem is fixed, and the phone screen shows a short tip too. Lens doesn't use the AI at all, so it works with no key and no limit.

**Other fixes found while testing**
- The pairing key is saved in `certs/`, so the phone's link keeps working after the server restarts.
- A scenario alert's text was being thrown away and replaced by an AI line with no details. It's now used as written.
- Vision requests on a cloud AI used an Ollama-only model name (always an error) and retried 3 times each. Fixed.
- The daily recap only goes out if the camera actually ran that day.
- The "your phone is missing" alert is skipped in phone mode (the phone is the camera).
- `.env.example` documents the new settings (`REMINDER_WARMUP_MIN`, `HABIT_MIN_GAP_SEC`, `IDLE_CHAT_MIN_SEC`, `AI_PAUSE_ON_LIMIT_SEC`).

**How it was tested:** a fake AI server that can pretend to be out of quota, a simulated phone in a headless browser (fake camera video plus fake motion-sensor readings for "turned sideways", "lying flat", and "back upright"), scripted dark and bumped frames for Lens, and unit tests for the reminder rules, Lens checks, and desk-edge geometry. It hasn't been tried on a real iPhone yet, so the Turn button is there as a backup.

---

## Key design decisions (in plain words)

- **Why a web page on the phone instead of a native iPhone app?** It works on any phone with no App Store or Xcode setup, and the camera code is about 150 lines of JavaScript.
- **Why HTTPS?** Phone browsers only allow the camera on secure pages. The server makes its own certificate, which is why the phone shows a "not private" warning the first time.
- **Why one JPEG at a time instead of a video stream?** It's simple, needs no extra libraries, and the phone waits for each upload to finish. A slow connection lowers the frame rate instead of building up lag.
- **Why a pairing key?** Without it, anyone on the same Wi-Fi could send fake frames. The key is only shown on the computer itself.
- **Why pause analysis when the phone disconnects?** Otherwise the characters would react to an old frame, or log a fake "break" or "sip" when the phone comes back.
- **Why only send new frames?** The browser already shows the last frame, so sending it again just wastes Wi-Fi and processing time.
- **Why mark the desk by hand?** A camera can't reliably tell where a desk ends: desks are different colors and the view is at an angle. Four taps is quick and always right.
- **Why turn the frames on the phone instead of the computer?** Only the phone knows which way it's being held.
- **Why doesn't Lens use AI?** Brightness, blur, and tilt are simple measurements. Using AI for them would be slower and would use up the free limit the characters need for chatting.
