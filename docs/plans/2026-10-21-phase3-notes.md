# Phase 3 notes: what the FastAPI app has to match

From the route tests in `tests/characterization/`: 54 method + URL pairs (44 on the main server, 10 on the
phone server), 133 cases. To run them against FastAPI, change only `tests/characterization/client.py` and the
desk builder in `tests/characterization/conftest.py`. The coverage guard reads routes through
`client.routes()` / `client.route_for()`.

## Must stay exactly as today

**Error replies and status codes**
- Error replies are `{"error": "..."}` with the same status codes (403 / 400 / 409 / 404), not FastAPI's
  `{"detail": ...}`.
- `/api/activity` returns 204 with an empty body. FastAPI's default is 200.
- Trailing slashes: `/api/live/` is 404 today. Turn off `redirect_slashes`, which would make it a 307.
  `/phone` and `/phone/` are both real pages.

**Keys left out vs null**
- `tip` and `reply_to` are left out when there's nothing, while `actions: []` and `tip: null` (Pi upload)
  are always sent. Pydantic reply models must match this exactly.
- 1-on-1 chat replies have no message `id`; group chat replies do.

**SSE (`/api/stream`)**
- Content type `text/event-stream; charset=utf-8`.
- Headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- The first event is exactly `retry: 2000`, then one-line `data: {json}` events.
- A keepalive comment every 25 s (not tested).

**Video**
- Content type `multipart/x-mixed-replace; boundary=frame`; each part has `Content-Type` and `Content-Length`.
- The stream sends nothing at all, not even headers, until the first picture exists.

**Other headers**
- CORS `Access-Control-Allow-Origin: *` on every main-server reply; none on the phone server.
- `Cache-Control: no-cache` on static files.
- `public, max-age=3600` on saved photos.
- No-cache, `Pragma` and `Expires` headers on live pictures.

**Local vs Wi-Fi requests**
- `/api/live` and `/api/phone/info` include the phone link, QR code and Pi command only when the request comes
  from 127.0.0.1.
- FastAPI's TestClient reports the client as `testclient`, so the adapter must set it to 127.0.0.1.

**Headers the Pi and phone send**
- From the Pi: `X-Desk-Key`, `X-Frame-Id`, `X-Taken-At`, `X-Pi-Camera`, `X-Pi-Photos`, `X-Pi-Free-MB`,
  `X-Pi-Last-Cleanup`, `X-Lux` (upload format 1; blank values are ignored). `desktalk_shared.wire.info_from_v1_headers`
  reads them.
- From the phone: `X-Desk-Rotation` and `X-Desk-Gravity`.

**Endless streams**
- TestClient may not allow reading only the start of an endless SSE or video reply. If it doesn't, `stream()` in
  `client.py` may need a real uvicorn server on a spare port.

## Odd behavior today (recorded by the tests as it is)

Decide on purpose in Phase 3, and re-record the snapshot (`DESKTALK_RECORD_SNAPSHOTS=1`) only for the ones we fix:

- An empty upload to `/api/pi/frame` or `/api/phone/frame` gives 500 (OpenCV crashes on an empty buffer).
  **Fix:** 400 with `{"error": "not a JPEG image"}`, like other bad pictures.
- `/api/habit_log?limit=lots` and `/api/log/events?limit=all` give 500. **Fix:** 400 with a plain error.
- The phone server's automatic `/static/<path>` route always gives 404 (the folder doesn't exist). **Fix:** drop the route.
- Pi upload replies always include `"tip": null`; phone upload replies leave `tip` out. Keep as is (the pages
  don't mind).
