/* ============================================
   DESK TALK — phone camera page
   Grabs frames from the phone camera and sends them to the
   computer as JPEGs over Wi-Fi (POST /api/phone/frame).
   ============================================ */

const TARGET_FPS = 10;     // frames per second sent to the computer
const MAX_SIDE = 960;      // longest side of each frame in pixels (YOLO works at 640)
const JPEG_QUALITY = 0.7;

const $ = (id) => document.getElementById(id);
const video = $('preview');
const canvas = $('grab');
const ctx = canvas.getContext('2d');

// The pairing key comes from the QR code link (…/phone?key=abc123).
const pairKey = new URLSearchParams(location.search).get('key') || '';

let stream = null;
let streaming = false;
let facingMode = 'environment';   // back camera first
let wakeLock = null;
let sentCount = 0;
let lastStatsTime = performance.now();

// ---------- keeping the picture upright ----------
// If the phone's screen rotation is locked and you turn the phone sideways, the camera
// keeps sending the picture "portrait", so the desk shows up on its side on the computer.
// The motion sensor tells us which way is really up, and we turn each frame to match.
// The "Turn" button adds an extra quarter turn by hand, in case a phone gets it wrong.
const IS_IOS = /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
let gravity = null;          // latest [x, y, z] from the motion sensor
let phoneTurn = null;        // how the phone is physically turned: 0 / 90 / 180 / 270 clockwise (null = unknown)
let candidateTurn = 0;
let candidateSince = 0;
let extraTurn = 0;           // from the Turn button (saved on this phone)
// Safari usually reports the motion sensor with the opposite sign from Chrome. If this phone
// turns out to be different, the check in onMotion() notices and saves the right sign.
let motionSign = IS_IOS ? -1 : 1;
let mismatchSince = 0;
try {
  extraTurn = (+localStorage.getItem('desktalk_extra_turn') || 0) % 360;
  const savedSign = +localStorage.getItem('desktalk_motion_sign');
  if (savedSign === 1 || savedSign === -1) motionSign = savedSign;
} catch (_) { /* private mode */ }

/** How the screen itself is turned (0, 90, 180 or 270). */
function screenAngle() {
  const a = screen.orientation?.angle ?? window.orientation ?? 0;
  return ((a % 360) + 360) % 360;
}

function onMotion(e) {
  const g = e.accelerationIncludingGravity;
  if (!g || g.x == null) return;
  const x = motionSign * g.x;
  const y = motionSign * g.y;
  gravity = [g.x, g.y, g.z ?? 0];
  // When the phone lies almost flat (looking straight down), "up" is unclear: keep the last answer.
  if (Math.hypot(x, y) < 4) return;
  const deg = ((Math.atan2(-x, y) * 180) / Math.PI + 360) % 360;   // 0 = phone upright
  const snapped = (Math.round(deg / 90) * 90) % 360;
  const off = Math.abs(((deg - snapped + 540) % 360) - 180);
  if (off > 30) return;                                              // too close to a diagonal
  const now = performance.now();

  // Self-check: when the screen has turned sideways by itself, the phone must be turned the
  // same way. If the sensor keeps saying the exact opposite, our sign is backwards: flip it.
  const sa = screenAngle();
  if (sa === 90 || sa === 270) {
    const expected = (360 - sa) % 360;
    if (snapped === (expected + 180) % 360) {
      if (!mismatchSince) mismatchSince = now;
      if (now - mismatchSince > 1500) {
        motionSign = -motionSign;
        mismatchSince = 0;
        phoneTurn = null;
        try { localStorage.setItem('desktalk_motion_sign', String(motionSign)); } catch (_) { /* ignore */ }
      }
      return;
    }
  }
  mismatchSince = 0;

  if (snapped !== candidateTurn) { candidateTurn = snapped; candidateSince = now; }
  if (phoneTurn === null || (snapped !== phoneTurn && now - candidateSince > 700)) phoneTurn = snapped;
}

/** Clockwise turn to apply to each frame so the desk is the right way up. */
function turnDegrees() {
  // If the screen turned with the phone, the camera picture is already upright
  // (phoneTurn + screenAngle() comes out as 0). If rotation lock kept the screen
  // still, this is how far the picture needs turning. Without a sensor: no turn.
  const auto = phoneTurn === null ? 0 : (phoneTurn + screenAngle()) % 360;
  return (auto + extraTurn) % 360;
}

async function startMotion() {
  // iPhone asks for permission first; this must run straight from the Start tap.
  try {
    if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
      const answer = await DeviceMotionEvent.requestPermission();
      if (answer !== 'granted') return;
    }
    window.addEventListener('devicemotion', onMotion);
  } catch (_) { /* no motion sensor: the Turn button still works */ }
}

function setStatus(text, state) {
  $('status').textContent = text;
  $('status').dataset.state = state;
}

function showError(message) {
  $('error').textContent = message;
  $('error').hidden = false;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- camera ----------
async function openCamera() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {
      facingMode: { ideal: facingMode },
      width: { ideal: 1280 },
      height: { ideal: 720 },
    },
  });
  video.srcObject = stream;
  await video.play();
}

async function keepScreenOn() {
  try {
    if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen');
  } catch (_) { /* not supported or denied — the user can turn off auto-lock */ }
}

// ---------- sending frames ----------
const sent = $('sent');
const sentCtx = sent.getContext('2d');
let lastThumb = 0;

function grabFrame() {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!w || !h) return Promise.resolve(null);
  const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
  const fw = Math.round(w * scale);
  const fh = Math.round(h * scale);
  const turn = turnDegrees();
  const sideways = turn % 180 !== 0;
  const cw = sideways ? fh : fw;
  const ch = sideways ? fw : fh;
  if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
  ctx.setTransform(1, 0, 0, 1, cw / 2, ch / 2);
  ctx.rotate((turn * Math.PI) / 180);
  ctx.drawImage(video, -fw / 2, -fh / 2, fw, fh);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  drawThumb();
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', JPEG_QUALITY));
}

/** Small copy of exactly what the computer gets, so you can check it's the right way up. */
function drawThumb() {
  const now = performance.now();
  if (now - lastThumb < 400) return;
  lastThumb = now;
  const side = 132;
  const k = side / Math.max(canvas.width, canvas.height);
  const tw = Math.round(canvas.width * k);
  const th = Math.round(canvas.height * k);
  if (sent.width !== tw || sent.height !== th) { sent.width = tw; sent.height = th; }
  sentCtx.drawImage(canvas, 0, 0, tw, th);
}

let tipShown = '';
function showTip(text) {
  if ((text || '') === tipShown) return;
  tipShown = text || '';
  $('tip').textContent = tipShown;
  $('tip').hidden = !tipShown;
}

async function sendLoop() {
  while (streaming) {
    const started = performance.now();
    const jpeg = await grabFrame();
    if (jpeg) {
      try {
        const headers = { 'Content-Type': 'image/jpeg', 'X-Desk-Key': pairKey, 'X-Desk-Rotation': String(turnDegrees()) };
        if (gravity) headers['X-Desk-Gravity'] = gravity.map((v) => v.toFixed(2)).join(',');
        const res = await fetch('/api/phone/frame', { method: 'POST', headers, body: jpeg });
        if (res.status === 403) {
          // The pairing key changed (e.g. the certs folder was deleted).
          stopStreaming(false);
          showError('This link is out of date. Scan the QR code on your computer again.');
          return;
        }
        if (res.ok) {
          sentCount++;
          setStatus('Live on your computer', 'live');
          const info = await res.json().catch(() => null);
          showTip(info?.tip);   // e.g. "Too dark", from Lens on the computer
        }
      } catch (_) {
        setStatus('Reconnecting…', 'warn');
        await sleep(1000);
      }
    }
    updateStats();
    // Wait so we send about TARGET_FPS frames per second.
    const wait = 1000 / TARGET_FPS - (performance.now() - started);
    if (wait > 0) await sleep(wait);
  }
}

function updateStats() {
  const now = performance.now();
  if (now - lastStatsTime < 1000) return;
  const fps = (sentCount * 1000) / (now - lastStatsTime);
  $('stats').textContent = `Sending ${fps.toFixed(0)} frames a second (${canvas.width}×${canvas.height})`;
  sentCount = 0;
  lastStatsTime = now;
}

// ---------- start / stop ----------
async function startStreaming() {
  $('error').hidden = true;

  if (!pairKey) {
    showError('Open this page by scanning the QR code on your computer (it has your pairing key).');
    return;
  }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    showError('The camera only works on the https:// link. Scan the QR code on your computer again.');
    return;
  }

  $('start').disabled = true;
  setStatus('Starting…', 'warn');
  startMotion();       // ask for the motion sensor during the tap (iPhone); not awaited
  try {
    await openCamera();
  } catch (err) {
    $('start').disabled = false;
    setStatus('Not streaming', 'error');
    if (err.name === 'NotAllowedError') {
      showError('Camera access was blocked. On iPhone: Settings → Safari → Camera → Allow, then reload this page.');
    } else {
      showError(`Couldn't open the camera (${err.name}). Close other apps using the camera and try again.`);
    }
    return;
  }

  await keepScreenOn();
  $('intro').hidden = true;
  $('controls').hidden = false;
  $('start').disabled = false;
  streaming = true;
  sendLoop();
}

function stopStreaming(tellComputer = true) {
  streaming = false;
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  window.removeEventListener('devicemotion', onMotion);
  $('controls').hidden = true;
  $('intro').hidden = false;
  showTip('');
  setStatus('Not streaming', 'idle');
  // Let the computer know this was on purpose, so Lens doesn't ask where the video went.
  if (tellComputer) {
    fetch('/api/phone/stop', { method: 'POST', headers: { 'X-Desk-Key': pairKey }, keepalive: true }).catch(() => {});
  }
}

function turnByHand() {
  extraTurn = (extraTurn + 90) % 360;
  try { localStorage.setItem('desktalk_extra_turn', String(extraTurn)); } catch (_) { /* ignore */ }
  lastThumb = 0;
}

async function flipCamera() {
  facingMode = facingMode === 'environment' ? 'user' : 'environment';
  try { await openCamera(); } catch (_) { /* keep the current camera */ }
}

// When you come back to this page (after locking the phone, etc.), turn things back on.
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState !== 'visible' || !streaming) return;
  await keepScreenOn();
  const track = stream?.getVideoTracks()[0];
  if (!track || track.readyState === 'ended') {
    try { await openCamera(); } catch (_) { stopStreaming(); }
  }
});

$('start').addEventListener('click', startStreaming);
$('stop').addEventListener('click', () => stopStreaming());
$('flip').addEventListener('click', flipCamera);
$('turn').addEventListener('click', turnByHand);
