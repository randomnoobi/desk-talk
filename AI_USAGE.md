# AI usage log

The Congressional App Challenge requires disclosing all AI use. This file lists every change made with an AI assistant (Claude, by Anthropic) so it can be copied into the submission. Everything not listed here was in the project before.

**Still Sam's original work:** YOLO detection and the camera loop, the relationship and danger-zone engine (`backend/relationships.py`), the character personalities and LLM prompts, habit tracking, lost-object tracking, the idle-chat director, the daily recap, and the website's text and scene drawings.

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

---

## Key design decisions (in plain words)

- **Why a web page on the phone instead of a native iPhone app?** It works on any phone with no App Store or Xcode setup, and the camera code is about 150 lines of JavaScript.
- **Why HTTPS?** Phone browsers only allow the camera on secure pages. The server makes its own certificate, which is why the phone shows a "not private" warning the first time.
- **Why one JPEG at a time instead of a video stream?** It's simple, needs no extra libraries, and the phone waits for each upload to finish. A slow connection lowers the frame rate instead of building up lag.
- **Why a pairing key?** Without it, anyone on the same Wi-Fi could send fake frames. The key is only shown on the computer itself.
- **Why pause analysis when the phone disconnects?** Otherwise the characters would react to an old frame, or log a fake "break" or "sip" when the phone comes back.
- **Why only send new frames?** The browser already shows the last frame, so sending it again just wastes Wi-Fi and processing time.
