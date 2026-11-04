# Pictures from the Pi: what is sent, how much, and a plan

From the meeting after the branch cleanup: *check whether the Raspberry Pi sends raw pictures or detection
results to the server, estimate the amount of data sent, consider whether a preview is needed, and lay out
the connection protocol and the picture transfer plan.*

> **Status: settled by a bigger change.** This page was written when the Pi sent every picture over Wi-Fi to a Mac
> running the app, and it planned how to send fewer. The Pi now runs the app itself: the camera container hands each
> picture to the app container on the same Pi, over Docker's own network, so **no picture crosses Wi-Fi to be
> detected or analysed**, and the plan (send detections, and a picture only when asked) was not needed and was not built.
> What is still true is below: what one upload holds and how big pictures are (sections 1 to 3, now between two
> containers), and what the app uses a picture for. What was a plan for the Wi-Fi link (sections 4 to 6 before) is
> cut down to a note at the end. The sizes were measured on a Mac with Sam's own desk photos taken by the Pi
> camera; uploads were never timed on the Pi.

## 1. The answer: with every picture, the camera sends both

Every picture the camera takes (3 a second) goes to the app as a **full 1280×720 JPEG**, with **what the detector found
attached** to the same upload. Nothing is held back: pictures the detector had no time for go too, marked `busy`, only
for the live view.

1. In the `camera` container, `raspberry_pi/camera/main.py` (`CameraProgram.step`) takes a picture every 1/fps
   seconds, saves it in its `/photos` volume, and offers it to the detector (`detector_client.py`,
   `DetectorWorker`). The detector takes one picture at a time. Pictures taken while it's busy go out as
   `busy`; when the detector is down they go out as `off`, with the reason.
2. `CameraProgram.deliver` builds an `UploadInfo` (`shared/desktalk_shared/wire.py`) and hands the JPEG and
   the info to the uploader (`raspberry_pi/camera/uploader.py`). Only the newest waiting picture is kept, so
   a slow app skips pictures instead of the camera falling behind. A `busy` picture never pushes out one that
   carries detections or still needs detecting.
3. The uploader sends **one `POST /api/pi/frame` per picture** in **upload format 2**
   (`wire.VERSION = 2`): `multipart/form-data` with two parts:
   - `info`: JSON with `version`, `frame_id` (e.g. `pi-000123`), `taken_at`, `lux`, `detector`
     (`ok` / `busy` / `off`), `detector_reason`, `detections`, `detect_ms`, `model` and `health` (which
     camera, pictures stored, disk free, last cleanup, processor temperature, the slow-down flags it can read).
   - `picture`: the JPEG.
4. In the `app` container, `desktalk/web/routers/pi.py` checks the key, reads the body (at most
   `web.pi_upload_max_mb`), decodes it with `wire.decode_upload`, and hands it to `PiFrameSource`
   (`desktalk/adapters/camera/pi_source.py`, on top of `push_source.py`). That **decodes every JPEG** and
   keeps only the newest picture.
5. `DetectionProducer` (`desktalk/core/vision/producer.py`) uses the detector's detections when the state is `ok`,
   shows `busy` pictures in the live view only, and for `off` makes no event on the Pi, where the app has no detector
   of its own (a computer running the app does run YOLO on it).
6. The reply is `PiFrameReply` (`desktalk/web/schemas.py`): `{"ok": true, "fps": 2.9, "tip": null}`.
   The camera doesn't read it: `Uploader._send` only looks at the status code.

**Upload format 1** (the old `raspberry_pi/camera_node.py`, no longer in the repo) is still accepted: any
upload that isn't `multipart/form-data` is read as a bare JPEG body with `X-Frame-Id`, `X-Taken-At`,
`X-Lux`, `X-Pi-Camera`, `X-Pi-Photos`, `X-Pi-Free-MB` and `X-Pi-Last-Cleanup` headers
(`wire.info_from_v1_headers`). Its detector state is always `off`.

How fast the detector really is: measured on a Pi 5 (8 GB) with the real camera and the whole stack running, all 3
pictures a second reach the app and the detector answers about 1.5 a second (`yolo26m-objv1-150`, 640 pixels, ncnn),
so about half go out as `ok` and half as `busy`. Not a final number: the Pi speed test picks the final model.

## 2. How much data

### What was measured

- **Pictures**: Sam's 6 desk photos taken with the Pi camera (`data/bench/desk-photos/desk_01.jpg` to
  `desk_06.jpg`, 1280×720, not in git), turned into JPEGs again with OpenCV at the sizes below. The Pi
  camera module makes its JPEGs with Picamera2's own encoder, so real sizes may differ a little; the saved
  files were within 0.3% of the OpenCV quality-80 sizes.
- **The info part**: a real `UploadInfo` with 7 detections (laptop, bottle, book, mouse, phone, earphone,
  person) and a full `health`, put through `wire.encode_upload`.
- **The HTTP request**: the exact bytes the Pi's `net.call` (Python's `urllib`) sends, caught by a small
  socket server on the Mac, and a reply like uvicorn's.

| Picture | Smallest | Average | Largest |
|---|---|---|---|
| 1280×720, quality 80 (today: `pi.width`, `pi.height`, `pi.jpeg_quality`) | 65,866 B | **76,071 B** | 85,002 B |
| 1280×720, quality 70 | 59,992 B | 69,024 B | 76,960 B |
| 960×540, quality 70 (about what the phone camera sends) | 34,717 B | 40,354 B | 45,144 B |
| 640×360, quality 70 | 20,061 B | 23,122 B | 25,980 B |
| 640×360, quality 60 | 17,123 B | 19,694 B | 21,992 B |
| 320×180, quality 60 | 6,761 B | 7,471 B | 8,117 B |

| Part of one upload | Bytes |
|---|---|
| `info` JSON, no detections / 3 / 7 | 353 / 647 / 1,051 |
| `info` part with 7 detections, plus the multipart framing | 1,371 |
| HTTP request headers (`POST`, `Host`, `Content-Type` with the boundary, `X-Desk-Key`, `Connection: close` ...) | 264 |
| Reply (`PiFrameReply` and uvicorn's headers) | 157 |
| **One upload today, average picture** (HTTP only) | 264 + 1,371 + 76,071 + 157 = **77,863** |
| **One upload with no picture** (HTTP only) | 264 + 1,371 + 157 = **1,792** |

On top of HTTP comes TCP/IP: about 66 bytes per packet, and `urllib` opens a **new connection for every
upload** (`Connection: close`), so each one also pays for the connection setup and close. Estimated with
1,448 bytes per packet: **83,671 B** for an average upload with a picture, **2,452 B** without one.

### The arithmetic

```
83,671 bytes an upload × 3 uploads a second  = 251,013 bytes a second ≈ 251 kB/s
251 kB/s × 8                                 ≈ 2,008 kbit/s ≈ 2.0 Mbit/s
251 kB/s × 3,600 seconds                     ≈ 904 MB an hour
```

(kB, MB and GB here are 1,000, 1,000,000 and 1,000,000,000 bytes.) That was the load on the Wi-Fi when the app ran on a Mac:
about 2 Mbit/s all day, and 21.7 GB a day. **It no longer crosses Wi-Fi.** The camera and the app are on the same Pi, so
those bytes go over Docker's own network, and the Wi-Fi carries only a page someone has open. The page's live view
sends one picture stream (`GET /api/video_feed...`) only while a page is open; how big that is hasn't been measured. A
test on the real Pi checks that no more than 15,000 bytes leave it in 90 seconds, half of the smallest picture measured
(`test_no_picture_goes_off_the_pi`), which is how "pictures never leave the Pi" is kept true.

What does still cost the Pi is its own SD card: the camera writes every picture to its card, about 0.8 GB an hour
(76 kB × 3 × 3,600), and deletes them all every 10 minutes (`pi.delete_every_min`). The app also decodes each JPEG
(about 1 ms on a Mac, more on the Pi) and `DetectionProducer._show` makes a second JPEG with the boxes drawn, 3 times
a second.

## 3. What the app uses the picture for

| App feature | What it needs from the picture | How often | Code | Without the full picture? |
|---|---|---|---|---|
| **Detection by the app itself** (a computer running the app only: the Pi's app has none) | the whole picture, for YOLO | every picture marked `off` (or format 1) | `producer.py` `_find` | No: these must keep the picture |
| **Light level in each event** | average brightness of a 64-pixel-wide grey copy (`camera.light_measure_width`), plus the Pi's `lux` (camera module only) | every analysed picture | `producer.py` `measure_light`, `_event`; used by `habits/habit_rules.py` (`is_bright`, `is_dim`) | Yes, if the Pi sends the brightness number |
| **Picture size in each event** | width and height (events, clipping boxes with `keep_inside`) | every analysed picture | `producer.py` `_find`, `_event` | Yes, if the Pi sends the size |
| **Lens camera checks**: covered, dark, glare, blurry | brightness, share of white pixels, sharpness of a 240-pixel-wide grey copy (`lens.measure_width`) | about once a second (`lens.sample_every_sec`), analysed pictures only | `core/vision/camera_health.py` `sample`, `camera_measure.py` | Yes, if the Pi measures and sends the numbers |
| **Movement** ("something moved"): the `moved` flag in each event, which object states and habits use as "you're at the desk" | how much the grey copy changed since the last one (`lens.motion_above`) | about once a second | `camera_health.py` `moved_recently`; `object_states/tracker.py` (`person_counts_movement`), `habits/habit_reader.py` | Yes, if the Pi sends the change number |
| **Lens, cut-off objects** | only the boxes and the picture size | every couple of seconds | `camera_problems.py` | Yes |
| **Live view** in the app (plain or with boxes) | every picture, 3 a second; the app draws boxes, desk edges and danger zones on it (`adapters/picture/painter.py`) | only while a page shows it (MJPEG, `GET /api/video_feed...`) | `web/routers/video.py`, `web/streams.py` | Only while someone watches (section 4) |
| **Marking the desk corners** | one plain picture | when you open that page or press the button | `GET /api/latest_frame_raw/<camera>`, `web_app/app.js` | One picture on request |
| **Proof photos** (alerts, Lens problems, nail biting, slouching, lost items, desk scans) | the newest picture with the boxes drawn, cropped around the objects | when something happens; a desk scan photo at most every 120 s (`lost_items.snapshot_every_sec`) | `core/vision/proof_photos.py` (`ProofPhotos.save`) | One fresh picture on request |
| **Vision AI: posture, screen distance, phone in hand** (Ollama only) | the newest plain picture | every 42 s (`vision_habits.every_sec`), only while you're at the desk | `chat/workers/vision_habits.py` | One picture every 42 s, a few seconds old is fine |
| **Lost items: has the desk changed?** | a 64×64 grey copy of the newest picture | every 60 s (`lost_items.check_every_sec`) | `chat/workers/lost_items.py` `_scan_due` | One picture a minute |
| **Lost items: vision scan** | the newest plain picture | when the desk changed, or at least every 300 s (`lost_items.rescan_every_sec`) | `lost_items.py` `_scan` | One picture a minute at most |
| **"Have you seen my keys?"** | the newest plain picture | when you ask | `chat/workers/lost_items_answers.py` | One fresh picture on request |
| Safety alerts, agents joining, object states, habits | only the detections | every analysed picture | `desk_monitor.py`, `roster`, `object_states/` ... | Yes, already |

So apart from the live view, the app needs a full picture **about once a minute**, plus one **right away** when something
happens (an alert, a question, the desk-marking page). Everything it needs 3 times a second is a handful of numbers.
The pictures that go to a vision AI (the posture and lost-item checks, "have you seen my keys?") go only to Ollama on a
computer, never to `llm.ai_url`, and never from a Pi (`llm.send_pictures: false` in `raspberry_pi/settings.pi.yaml`).

## 4. What came of the plan

The old plan (send detections always, and a picture only when the Mac asked for one, about 17 times less data) was for
a Pi that sent every picture over Wi-Fi to a Mac. With the app on the Pi, it was not built, and nothing in it is needed:
no upload format 3, no `pictures` field in the reply, no Pi-side measuring of Lens's numbers. Its open questions:

1. *Is the app page open most of the day?* Only a page that is open streams the live view over Wi-Fi, so this now only
   affects how busy the Wi-Fi is, and the Pi's own work to draw the live view.
2. *How old may the picture for the AI checks be?* Moot: the app has the newest picture.
3. *Proof photos* take the newest picture right away, as before.
4. *The Pi's Wi-Fi speed* only matters for the page and the optional cloud link (events only), not for pictures.
5. *Pictures on the Pi's SD card* are still written and deleted every 10 minutes (above). Keeping them is still the
   camera's job; `pi.keep_last_min` is 0.
6. *The detector's speed*: about 1.5 pictures a second at the medium model and 640 pixels, so half the pictures are `busy`.
   The speed test picks the final model, which changes how quickly characters react.

How the camera reaches the app today: the app makes the pairing key and writes it in the `pairing` volume; the
camera reads it and sends every request with it in `X-Desk-Key`; the app compares it in constant time
(`web/deps.py` `key_check`). The camera asks `GET /api/pi/config` for the `pi:` settings at its start and every
`pi.settings_check_every_sec`; if the app can't be reached it starts with its own copy of the settings. When an upload fails
(no answer, or an error status) the camera logs it once, waits `pi.upload_retry_start_sec`, doubling up to
`pi.upload_retry_max_sec`, and then sends only the newest picture: old pictures are never sent later, since old detections
would make agents join minutes late. The reply codes: 200 with `PiFrameReply`, 400 for a broken upload, 403 for a wrong
key, 409 when `camera.source` isn't `pi`, 413 over `web.pi_upload_max_mb` (4 MB). Plain HTTP, no TLS, on Docker's network.
