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
function grabFrame() {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!w || !h) return Promise.resolve(null);
  const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', JPEG_QUALITY));
}

async function sendLoop() {
  while (streaming) {
    const started = performance.now();
    const jpeg = await grabFrame();
    if (jpeg) {
      try {
        const res = await fetch('/api/phone/frame', {
          method: 'POST',
          headers: { 'Content-Type': 'image/jpeg', 'X-Desk-Key': pairKey },
          body: jpeg,
        });
        if (res.status === 403) {
          // The server restarted and made a new pairing key.
          stopStreaming();
          showError('Your computer restarted Desk Talk, so this link is out of date. Scan the new QR code on your computer.');
          return;
        }
        if (res.ok) {
          sentCount++;
          setStatus('Live on your computer', 'live');
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

function stopStreaming() {
  streaming = false;
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  $('controls').hidden = true;
  $('intro').hidden = false;
  setStatus('Not streaming', 'idle');
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
$('stop').addEventListener('click', stopStreaming);
$('flip').addEventListener('click', flipCamera);
