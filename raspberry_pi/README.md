# Raspberry Pi camera

The Pi is Desk Talk's camera. It takes **3 pictures a second**, keeps them on
its own storage, sends each one to your computer over Wi-Fi, and **deletes the
pictures every 10 minutes** so its storage never fills up. Your computer does
the object detection and keeps the event log.

```
Raspberry Pi (camera_node.py)                 Your computer (python server.py)
┌──────────────────────────────┐   pictures   ┌──────────────────────────────────────┐
│ camera → JPEG, 3 per second  │ ───────────▶ │ /api/pi/frame → YOLO → detection     │
│ saved in ~/desk-talk-photos  │   Wi-Fi      │ events → event stream → event log     │
│ deleted every 10 minutes     │              │ (data/events/) + agents + habits      │
└──────────────────────────────┘              └──────────────────────────────────────┘
```

## Set up the Pi (once)

Raspberry Pi OS (Bookworm) already has Python 3, Picamera2 and PyYAML. On the Pi:

```bash
sudo apt update
sudo apt install -y git python3-picamera2 python3-opencv python3-yaml
git clone https://github.com/randomnoobi/desk-talk.git
cd desk-talk
```

Only `raspberry_pi/camera_node.py` runs on the Pi; nothing else needs installing there.

## Run it

1. On your computer, start `python server.py`. It prints the exact command for the Pi, with
   your computer's address and the pairing key, for example:

   ```bash
   python3 raspberry_pi/camera_node.py --server http://192.168.1.20:5000 --key AbC123xY
   ```

   (The app shows the same command in the Desk cam panel and in Settings.)
2. Run that command on the Pi, in the `desk-talk` folder.
3. The Desk cam panel on your computer switches to the live picture.

The Pi gets its settings (pictures per second, cleanup time...) from your
computer's `config/settings.yaml` (`pi:` section) when it starts. Flags override them:

| Flag | Default | What it does |
|---|---|---|
| `--fps` | 3 | pictures per second |
| `--delete-every-min` | 10 | delete the saved pictures this often |
| `--keep-last-min` | 0 | minutes of pictures a cleanup keeps (0 = delete all) |
| `--photo-dir` | `~/desk-talk-photos` | where pictures are saved |
| `--min-free-mb` | 300 | if the disk gets this full, delete the oldest pictures right away |
| `--camera` | `auto` | `picamera2` (camera module), `usb` / `usb:1` (USB webcam), `folder:<dir>` or `video:<file>` for testing |
| `--offline` | | only take and save pictures, send nothing |

The Pi camera module also reports the room's light level (lux), which the
light habit (sunlight, dim lights at night) uses.

## Start it automatically when the Pi boots (optional)

```bash
sudo tee /etc/systemd/system/desk-talk-camera.service > /dev/null <<EOF
[Unit]
Description=Desk Talk camera
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$HOME/desk-talk
ExecStart=/usr/bin/python3 raspberry_pi/camera_node.py --server http://192.168.1.20:5000 --key AbC123xY
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now desk-talk-camera
journalctl -u desk-talk-camera -f      # see what it's doing
```

Put your own address and key in the `ExecStart` line (the ones `server.py` printed).

## If something's wrong

- **"Cannot reach the computer"**: both on the same Wi-Fi? Is `server.py` running? On a Mac,
  allow incoming connections for Python if the firewall asks.
- **"Pairing key does not match"**: copy the command `server.py` printed again (the key is
  saved in `certs/pair-key.txt` on your computer).
- **"This computer is set to camera.source: phone"**: set `camera: source: pi` in
  `config/settings.yaml` on your computer and restart it.
- **Camera problem**: `libcamera-hello` (or `rpicam-hello`) should show a preview; check the
  ribbon cable. For a USB webcam use `--camera usb`.
