# Detection

The camera finds what's on the desk with YOLO26 trained on Objects365, which knows 365 kinds of objects, including
earphones, keys, pens, watches, glasses and notepaper.

On the Pi, the detector container runs yolo26s-objv1-150.pt (the small one) at 640 pixels, as an ncnn model, the
fastest kind on the Pi's processor (pi.detector in raspberry_pi/settings.pi.yaml). On a Pi 5 (8 GB) it keeps up
with all 3 pictures a second, about 210 ms each. If the detector is off, no objects are found until it's back; the
live view still works.

On a computer, for development with a phone or webcam, the app finds the objects itself (detector in
config/settings.yaml) with yolo26m-objv1-150.pt (the medium one) and the same shared code. On an Apple-chip Mac it
runs on the GPU (device: auto); if it's too slow, use the small one.

An object joins the chat once it has been seen long and surely enough (3 seconds at 45% sure, at the Normal
sensitivity), and leaves after it has been gone 2 minutes. See [CHARACTERS.md](CHARACTERS.md#joining-and-leaving).

The safety checks look for danger zones, desk edges, chain reactions and objects coming close
(desktalk/core/vision/, tuned in config/safety_rules.json and the Settings page).

To pick another model for the Pi, see "Choosing the model" in [the Pi guide](../raspberry_pi/README.md#choosing-the-model).

## Model files

Model files (\*.pt) aren't in git. On the Pi, the install command fetches the Pi's model once and copies it into
the detector's volume; the detector never downloads anything itself. On a computer, server.py downloads the one it
needs into models/ the first time it starts. **On your Mac**, in the desk-talk folder:

```bash
uv run python scripts/download_models.py --test    # download the model and time it on this computer
```

```bash
uv run python scripts/download_models.py --list    # every object it can find
```
