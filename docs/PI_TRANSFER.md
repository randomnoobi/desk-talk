# Pictures from the Pi: what is sent, how much, and a plan

From the meeting after the branch cleanup: *check whether the Raspberry Pi sends raw pictures or detection
results to the server, estimate the amount of data sent, consider whether a preview is needed, and lay out
the connection protocol and the picture transfer plan.*

This is a plan only. Nothing in the code changes yet. The sizes below were measured on the Mac with Sam's
own desk photos taken by the Pi camera; the uploads themselves weren't timed on the Pi or the Wi-Fi.

## 1. The answer: the Pi sends both, with every picture

Every picture the Pi takes (3 a second) goes to the Mac as a **full 1280×720 JPEG**, with **what the Pi's
detector found attached** to the same upload. Nothing is held back: pictures the detector had no time for
go too, marked `busy`, only for the live view.

1. **On the Pi**, `raspberry_pi/camera/main.py` (`CameraProgram.step`) takes a picture every 1/fps
   seconds, saves it in `~/desk-talk-photos`, and offers it to the detector (`detector_client.py`,
   `DetectorWorker`). The detector takes one picture at a time. Pictures taken while it's busy go out as
   `busy`; when the detector is down they go out as `off`, with the reason.
2. `CameraProgram.deliver` builds an `UploadInfo` (`shared/desktalk_shared/wire.py`) and hands the JPEG and
   the info to the uploader (`raspberry_pi/camera/uploader.py`). Only the newest waiting picture is kept, so
   a slow Wi-Fi skips pictures instead of falling behind. A `busy` picture never pushes out one that
   carries detections or still needs detecting.
3. The uploader sends **one `POST /api/pi/frame` per picture** in **upload format 2**
   (`wire.VERSION = 2`): `multipart/form-data` with two parts:
   - `info`: JSON with `version`, `frame_id` (e.g. `pi-000123`), `taken_at`, `lux`, `detector`
     (`ok` / `busy` / `off`), `detector_reason`, `detections`, `detect_ms`, `model` and `health` (which
     camera, pictures stored, disk free, last cleanup, processor temperature, slow-down flags).
   - `picture`: the JPEG.
4. **On the Mac**, `desktalk/web/routers/pi.py` checks the key, reads the body (at most
   `web.pi_upload_max_mb`), decodes it with `wire.decode_upload`, and hands it to `PiFrameSource`
   (`desktalk/adapters/camera/pi_source.py`, on top of `push_source.py`). That **decodes every JPEG** and
   keeps only the newest picture.
5. `DetectionProducer` (`desktalk/core/vision/producer.py`) uses the Pi's detections when the state is `ok`,
   shows `busy` pictures in the live view only, and **runs YOLO on the Mac** when the state is `off`.
6. The reply is `PiFrameReply` (`desktalk/web/schemas.py`): `{"ok": true, "fps": 2.9, "tip": null}`.
   The Pi doesn't read it: `Uploader._send` only looks at the status code.

**Upload format 1** (the old `raspberry_pi/camera_node.py`, no longer in the repo) is still accepted: any
upload that isn't `multipart/form-data` is read as a bare JPEG body with `X-Frame-Id`, `X-Taken-At`,
`X-Lux`, `X-Pi-Camera`, `X-Pi-Photos`, `X-Pi-Free-MB` and `X-Pi-Last-Cleanup` headers
(`wire.info_from_v1_headers`). Its detector state is always `off`, so the Mac detects every picture.

How fast the Pi's detector really is: the model in `pi.detector` (`yolo26m-objv1-150`, 640 pixels, ncnn)
took about 600 ms a picture on the Pi 5 in the speed test
(`benchmarks/results/pi/2026-10-22T1650Z_pi.csv`: 1.7 pictures a second). So today about half the
pictures go out as `ok` and half as `busy`.

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

### The arithmetic, today

```
83,671 bytes an upload × 3 uploads a second  = 251,013 bytes a second ≈ 251 kB/s
251 kB/s × 8                                 ≈ 2,008 kbit/s ≈ 2.0 Mbit/s
251 kB/s × 3,600 seconds                     ≈ 904 MB an hour
904 MB × 24 hours                            ≈ 21.7 GB a day (the camera program runs all day,
                                               whether anyone is at the desk or not)
```

(kB, MB and GB here are 1,000, 1,000,000 and 1,000,000,000 bytes.) Counting HTTP only, without TCP/IP,
it's 77,863 × 3 × 8 ≈ 1,869 kbit/s; the earlier chat's 1,977 kbit/s is in the same range.

### Today and the options

| What the Pi sends | Bytes an upload (with TCP/IP) | Uploads a second | Data rate | An hour | A day |
|---|---|---|---|---|---|
| **Today**: full picture + detections with every upload | 83,671 | 3 | **2.0 Mbit/s** | 904 MB | 21.7 GB |
| Today, the busiest of the 6 photos | 93,196 | 3 | 2.2 Mbit/s | 1.0 GB | 24.2 GB |
| Detections only | 2,452 | 3 | 59 kbit/s | 26 MB | 0.64 GB |
| Detections only, at the Pi's real detection speed | 2,452 | 1.7 | 33 kbit/s | 15 MB | 0.36 GB |
| **Detections, plus one full picture every 10 s** (the plan below, nobody watching the live view) | | 3 | **120 kbit/s** | 54 MB | 1.3 GB |
| Preview for the live view, 640×360 quality 70 | 27,158 | 3 | 652 kbit/s | 293 MB | 7.0 GB |
| Preview, 640×360 quality 70 | 27,158 | 1 | 217 kbit/s | 98 MB | 2.3 GB |
| Small preview, 320×180 quality 60 | 10,451 | 1 | 84 kbit/s | 38 MB | 0.9 GB |

The plan's row: detections 2,452 B × 3 = 7.4 kB/s, plus a 76,071 B picture every 10 s = 7.6 kB/s, makes
15.0 kB/s ≈ 120 kbit/s. That's about **17 times less** than today.

The "detections only" figures assume the `info` part stays as it is (1,371 B with 7 detections). Most of the
remaining bytes are the HTTP and TCP/IP overhead of a fresh connection per upload, not the detections.

### What that means for the Wi-Fi

The Pi 4 and 5's built-in Wi-Fi usually manages a few tens of Mbit/s (more on 5 GHz close to the router,
much less through walls); it hasn't been measured on Sam's Wi-Fi. Against that, 2 Mbit/s is small but not
nothing:

- It's **all day, every day**, whether anyone is at the desk or not.
- If the Mac is on Wi-Fi too, each picture crosses the air **twice** (Pi → router → Mac), so it takes about
  4 Mbit/s of the room's Wi-Fi time.
- An average upload takes about 34 ms at 20 Mbit/s and 134 ms at 5 Mbit/s, so there's room. Below about
  2 Mbit/s an upload takes longer than the 333 ms between pictures; the uploader then skips pictures
  (it keeps only the newest), so the app slows down rather than falling behind.

Detections only (59 kbit/s) is so small that the Wi-Fi stops mattering.

The Mac also does work for every picture today: `PushFrameSource.push` decodes every JPEG (about 1 ms on
this Mac), and `DetectionProducer._show` makes a second JPEG with the boxes drawn, 3 times a second.

## 3. What the Mac uses the picture for

| Mac feature | What it needs from the picture | How often | Code | Without the full picture? |
|---|---|---|---|---|
| **Detection when the Pi's detector is off** | the whole picture, for YOLO | every picture marked `off` (or format 1) | `producer.py` `_find` | No: these must keep the picture |
| **Light level in each event** | average brightness of a 64-pixel-wide grey copy (`camera.light_measure_width`), plus the Pi's `lux` (camera module only) | every analysed picture | `producer.py` `measure_light`, `_event`; used by `habits/habit_rules.py` (`is_bright`, `is_dim`) | Yes, if the Pi sends the brightness number |
| **Picture size in each event** | width and height (events, clipping boxes with `keep_inside`) | every analysed picture | `producer.py` `_find`, `_event` | Yes, if the Pi sends the size |
| **Lens camera checks**: covered, dark, glare, blurry | brightness, share of white pixels, sharpness of a 240-pixel-wide grey copy (`lens.measure_width`) | about once a second (`lens.sample_every_sec`), analysed pictures only | `core/vision/camera_health.py` `sample`, `camera_measure.py` | Yes, if the Pi measures and sends the numbers |
| **Movement** ("something moved"): the `moved` flag in each event, which object states and habits use as "you're at the desk" | how much the grey copy changed since the last one (`lens.motion_above`) | about once a second | `camera_health.py` `moved_recently`; `object_states/tracker.py` (`person_counts_movement`), `habits/habit_reader.py` | Yes, if the Pi sends the change number |
| **Lens, cut-off objects** | only the boxes and the picture size | every couple of seconds | `camera_problems.py` | Yes |
| **Live view** in the app (plain or with boxes) | every picture, 3 a second; the Mac draws boxes, desk edges and danger zones on it (`adapters/picture/painter.py`) | only while a page shows it (MJPEG, `GET /api/video_feed...`) | `web/routers/video.py`, `web/streams.py` | Only while someone watches (section 4) |
| **Marking the desk corners** | one plain picture | when you open that page or press the button | `GET /api/latest_frame_raw/<camera>`, `web_app/app.js` | One picture on request |
| **Proof photos** (alerts, Lens problems, nail biting, slouching, lost items, desk scans) | the newest picture with the boxes drawn, cropped around the objects | when something happens; a desk scan photo at most every 120 s (`lost_items.snapshot_every_sec`) | `core/vision/proof_photos.py` (`ProofPhotos.save`) | One fresh picture on request |
| **Vision AI: posture, screen distance, phone in hand** (Ollama only) | the newest plain picture | every 42 s (`vision_habits.every_sec`), only while you're at the desk | `chat/workers/vision_habits.py` | One picture every 42 s, a few seconds old is fine |
| **Lost items: has the desk changed?** | a 64×64 grey copy of the newest picture | every 60 s (`lost_items.check_every_sec`) | `chat/workers/lost_items.py` `_scan_due` | One picture a minute |
| **Lost items: vision scan** | the newest plain picture | when the desk changed, or at least every 300 s (`lost_items.rescan_every_sec`) | `lost_items.py` `_scan` | One picture a minute at most |
| **"Have you seen my keys?"** | the newest plain picture | when you ask | `chat/workers/lost_items_answers.py` | One fresh picture on request |
| Safety alerts, agents joining, object states, habits | only the detections | every analysed picture | `desk_monitor.py`, `roster`, `object_states/` ... | Yes, already |

So apart from the live view and the Pi's detector being off, the Mac needs a full picture **about once a
minute**, plus one **right away** when something happens (an alert, a question, the desk-marking page).
Everything it needs 3 times a second is a handful of numbers.

## 4. Does the live view need a stream of pictures?

The live view is the only feature that needs pictures many times a second, and only while a page shows it.
Today the Mac gets every picture whether or not anyone is looking.

| Option | Data while nobody watches | Data while someone watches | Good | Bad |
|---|---|---|---|---|
| **A. Always** (today) | 2.0 Mbit/s | 2.0 Mbit/s | Nothing to build; the live view starts instantly | 21.7 GB a day for a view that's usually closed |
| **B. Only while an app page shows the live view** | 120 kbit/s | 2.0 Mbit/s | Same picture quality as today; the saving is all the time nobody watches | The Mac must count open live views; the view starts after one round trip (about 0.3–0.6 s) |
| **C. Always, but a smaller preview** (640×360 quality 70 at 3 a second) | 652 kbit/s | 652 kbit/s | Simple: no counting | Blurrier view; still 7 GB a day; proof photos and AI checks still need full pictures |

**Recommendation: B.** The Mac asks for pictures while at least one `GET /api/video_feed...` stream is open,
and stops asking when the last one closes. The Mac keeps drawing the boxes, desk edges and danger zones
(the Pi doesn't know about them). Whether the live view gets full-size pictures or a 640×360 preview while
it's open can be a setting; full size is the simpler start.

To count open live views, `LiveStreams.pictures` (`desktalk/web/streams.py`) would keep a count while each
MJPEG stream runs. Today `LiveStreams` only keeps track of the chat streams.

If the app page is open on a second screen all day, B saves little. That's the first open question below.

## 5. The connection today

The Pi always connects to the Mac; the Mac never connects to the Pi. The Pi runs no server other than the
detector container, which only answers on the Pi itself (`127.0.0.1:8100`).

1. **Pairing.** `server.py` on the Mac prints the command to run on the Pi, with the Mac's address and the
   pairing key (`certs/pair-key.txt`, `camera.pair_key_bytes`), e.g.
   `python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key AbC123xY`.
2. **Settings.** At start the Pi asks `GET /api/pi/config` for the `pi:` settings (repo settings → the
   Mac's settings → flags, `raspberry_pi/camera/cli.py`), and asks again every
   `pi.settings_check_every_sec` (60 s) (`SettingsWatcher`). If the Mac can't be reached it starts with
   the Pi's own copy of `config/settings.yaml`.
3. **Each picture.** `POST /api/pi/frame`, format 2, a new HTTP connection each time, waiting at most
   `pi.upload_timeout_sec` (5 s). Plain HTTP on the home network, no TLS.
4. **Key check.** Both requests carry the pairing key in `X-Desk-Key`. The Mac compares it in constant
   time (`web/deps.py` `key_check`) and answers 403 with how to fix it when it's wrong.
5. **The Mac's answers.** 200 with `PiFrameReply {ok, fps, tip}`. 400 when the upload is broken (bad
   `info`, wrong format version, not a JPEG), with what's wrong. 409 when the Mac's `camera.source` isn't
   `pi`. 413 when it's over `web.pi_upload_max_mb` (4 MB).
6. **When an upload fails** (no answer, or an error status), the Pi logs the problem once, waits
   `pi.upload_retry_start_sec` (1 s), doubling after each miss up to `pi.upload_retry_max_sec` (10 s), and
   then sends only the newest picture. Old pictures are never sent later: old detections would make agents
   join minutes late. The pictures stay on the Pi's disk until the next cleanup.
7. **Versions.** The `info` part carries its format version. A Mac that gets a newer format says "update
   this computer"; one that gets an older one says "update the Pi" (`UploadInfo.from_dict`). Format 1 has no
   `info` part and is read from its headers instead.

## 6. The proposed picture transfer plan

### The options

| Option | How | Data, nobody watching | Good | Bad |
|---|---|---|---|---|
| **1. Keep it as it is** | Full picture with every upload | 2.0 Mbit/s | Nothing to build; every Mac feature has the newest picture | 21.7 GB a day; busy Wi-Fi; the Mac decodes 3 JPEGs a second it mostly doesn't need |
| **2. Smaller pictures** | Lower `pi.jpeg_quality` or size, or skip `busy` pictures | 1.1–1.9 Mbit/s | A settings change | Worse live view and proof photos, and a smaller saving than it looks: quality 70 saves only 9% |
| **3. Detections always, pictures when the Mac asks** (recommended) | The Mac says in its reply to each upload whether it wants pictures; the Pi attaches one when asked | 120 kbit/s | 17 times less data; the Pi stays the one that connects, so nothing changes about addresses, the key or the firewall | Proof photos and AI checks may get a picture up to a few seconds old, or wait for the next one; more code on both sides |
| **4. The Mac fetches pictures from the Pi** | A small picture server on the Pi; the Mac asks it by picture id | 59 kbit/s + what it fetches | The Mac can get the exact picture an alert came from | The Mac has to reach the Pi: the Pi's address, a second key check, the Pi's firewall, two directions to keep working |
| **5. A separate video stream from the Pi** | Detections over HTTP as now, pictures as a video stream (MJPEG or RTSP) | 59 kbit/s while no one watches | Smooth live view | Two connections, with their own failures; the Mac must still match pictures to detections for proof photos |

### Recommendation: option 3

The Pi keeps sending one upload per analysed picture, with the detections and a few numbers about the
picture. A picture goes along only when the Mac asked for one, when the Pi's detector is off, or when the
last picture sent is older than a set time. The Mac says what it wants in the reply it already sends to
every upload, so the Pi keeps being the one that connects.

**What the Mac's reply would say** (a new field in `PiFrameReply`, e.g. `pictures`):

- `"every"`: attach the picture to every upload until told otherwise. While a live view is open
  (section 4), and while the desk-marking page is open.
- `"next"`: attach the picture to the next upload only. For a proof photo, an AI check, "have you seen my
  keys?", and `GET /api/latest_frame_raw`.
- `"none"`: no picture needed.

Every reply says it again, so the Pi only has to remember the last answer, and a Pi that restarts or
misses a reply is right again after the next upload.

**What the Pi would do on its own, whatever the Mac said:**

- Attach the picture when its detector is `off`: the Mac has to detect it. That costs the same as today.
- Attach one full picture at least every `pi.picture_every_sec` (proposed setting, 10 s), so the Mac
  always has one that's at most that old: the AI checks and the lost-items check keep working without
  waiting, and `GET /api/latest_frame_raw` always has something to show.
- Send `busy` pictures only when the Mac wants pictures: without a picture they carry nothing.
- Measure the Lens numbers about once a second (`lens.sample_every_sec`) and the brightness for every
  upload, from a small grey copy of the JPEG. OpenCV can decode a JPEG straight to a quarter-size grey
  picture (`cv2.IMREAD_REDUCED_GRAYSCALE_4`, 320×180), which took under 1 ms on this Mac; the Pi is
  slower, but it's once a second. The Pi already has OpenCV from apt (`python3-opencv`).

**Proof photos.** `ProofPhotos.save` takes the newest picture right away. With option 3 the newest picture
could be up to 10 s old, which may not show what the alert was about. Two ways:

- (a) Ask for `"next"` and save the photo when that picture comes, about one upload later (0.3–0.6 s). The
  photo's id is known at once and filled in a moment later. **Recommended to start.**
- (b) Ask the Pi for the exact picture the alert came from, by its id (`pi-000123`). The Pi has it on its
  disk (`photo_store.py`, the file name includes the id), but with `pi.keep_last_min: 0` every cleanup
  deletes all of them, so it would need `keep_last_min` of at least 1. Later, if (a) isn't good enough.

### What would change

| Where | What |
|---|---|
| `shared/desktalk_shared/wire.py` | **Upload format 3** (`VERSION = 3`): the `picture` part becomes optional; `info` gains the picture size (`width`, `height`), `brightness`, and the Lens numbers when measured (`brightness`, `glare`, `sharpness`, `change`). `encode_upload` takes no JPEG for an upload without a picture. `UploadInfo.from_dict` accepts **format 2 and 3** (an oldest-accepted version next to `VERSION`), so a Pi on format 2 keeps working during the switch; today it would be refused as too old. |
| `desktalk/web/schemas.py` | `PiFrameReply` gains `pictures: "every" \| "next" \| "none"`. `ok`, `fps` and `tip` stay. |
| `desktalk/web/routers/pi.py` | Accepts uploads without a picture; fills in `pictures` from what the Mac wants right now. `GET /api/pi/config` also says which upload formats this Mac reads (e.g. `upload_formats: [1, 2, 3]`) and the Lens measuring settings the Pi needs (`lens.measure_width`, `lens.white_level`, `camera.light_measure_width`). |
| A new small part on the Mac | Keeps who wants pictures: open live views (`web/streams.py`), the desk-marking page (`/api/latest_frame_raw`), `ProofPhotos`, `VisionHabits`, `LostItems`, `LostItemAnswers`. `pi.py` asks it for each reply. Its interface goes in `desktalk/ports.py` (with a contract suite in `tests/contracts/`), and `desktalk/bootstrap.py` builds it. |
| `desktalk/domain/camera.py`, `adapters/camera/push_source.py`, `pi_source.py` | A `Frame` can come without a picture (detections and numbers only). The newest picture is kept on its own, for the live view and the AI. |
| `desktalk/core/vision/producer.py` | Makes the event from the `info` when there's no picture: size and brightness from the Pi. Updates the live view only when a picture came. |
| `desktalk/core/vision/camera_health.py`, `camera_measure.py` | `sample` takes the Pi's numbers instead of measuring a picture; movement comes from the Pi's `change` number. Deciding when Lens speaks stays on the Mac (`camera_problems.py`). Pictures from the phone and the webcam are measured on the Mac as now. |
| `desktalk/core/vision/proof_photos.py` | Saves the photo when the next picture arrives (option (a) above). |
| `raspberry_pi/camera/uploader.py` | Reads the reply (today it only looks at the status code) and remembers `pictures`; attaches the picture when wanted, when the detector is `off`, or when the last one sent is older than `pi.picture_every_sec`; drops `busy` pictures when no picture is wanted. |
| `raspberry_pi/camera/main.py` and a new `raspberry_pi/camera/measure.py` | Measures the brightness and the Lens numbers from a small grey copy. The same math as `camera_measure.py`, with a test that both give the same numbers on the same pictures. It can't import `desktalk/`, and `desktalk_shared` is standard library only, so it lives in `raspberry_pi/camera/` and imports `cv2` inside the function. |
| `raspberry_pi/camera/settings.py` | Reads the new settings, and picks the newest upload format the Mac says it reads (format 2 when the Mac doesn't say). |
| `config/settings.yaml`, `desktalk/config/app.py` | New, with plain comments and typed fields: `pi.picture_every_sec` (10), and a wait for proof photos (e.g. `snapshots.wait_for_picture_sec`). |
| Tests | Format 3 encode and decode, format 2 still accepted, the reply field (and its characterization case for `/api/pi/frame`), the uploader's choices, the Pi's numbers matching the Mac's, the event made without a picture. With fakes, no network or Pi. |
| Docs | `README.md`, `docs/ARCHITECTURE.md`, `raspberry_pi/README.md`, `raspberry_pi/camera/README.md`, and this file. |

### Switching over

Update the Pi and the Mac together, from the same commit. The order still doesn't matter, so nothing
breaks while one is updated and the other isn't:

- **New Mac, old Pi**: the Pi keeps sending format 2 with a picture every time; the Mac takes it as today.
- **Old Mac, new Pi**: the Mac's `/api/pi/config` doesn't list `upload_formats`, so the Pi keeps sending
  format 2.
- **Both new**: the Pi sees format 3 in the list and switches.

The steps:

**On your Mac**: in the `desk-talk` folder, `git pull`, then stop and start `uv run python server.py`.

**On the Pi**: in the `desk-talk` folder, `git pull`, then restart the camera program
(`sudo systemctl restart desk-talk-camera` when it runs as the service, or stop it and run the command
`server.py` prints again).

**On your Mac**: open `http://localhost:5000/api/health` and check that `pi.upload_format` says `3`, and that the live view
still moves while the app page is open.

Format 2 can be removed once the Pi has run format 3 for a while.

## 7. Open questions for Sam

1. **Is the app page open most of the day** (on a second screen, say)? If so, the live view keeps asking
   for every picture and option 3 saves much less. Should the live view then get a 640×360 preview
   (652 kbit/s) instead of full pictures?
2. **How old may the picture for the AI checks be?** The plan uses one full picture at least every 10 s.
   Longer saves more (every 30 s: about 80 kbit/s in all).
3. **Proof photos**: is a photo taken about half a second after the alert good enough, or do they need the
   exact picture the alert came from (option (b), which needs the Pi to keep its last minute of pictures)?
4. **The Pi's Wi-Fi**: 2.4 or 5 GHz, and how far from the router? It's worth measuring once:
   **on your Mac** `brew install iperf3` then `iperf3 -s`, and **on the Pi** `sudo apt install iperf3`
   then `iperf3 -c <the Mac's address>`.
5. **Pictures on the Pi's SD card**: the Pi writes every picture to its card, about 0.8 GB an hour (76 kB ×
   3 × 3,600), and deletes them all every 10 minutes. Is keeping them still needed? If yes, should a cleanup
   leave the last minute (`pi.keep_last_min: 1`) so the Mac could ask for an exact picture later?
6. **The Pi's detector speed**: at 640 pixels the current model does about 1.7 pictures a second on the Pi 5,
   so half the pictures are `busy`. The speed test's own pick runs it at 480 pixels (3.2 a second). Should
   that change first? It doesn't change the data much with option 3, but it changes how quickly agents react.
