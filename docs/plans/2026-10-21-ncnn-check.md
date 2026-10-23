# Can the app's YOLO model run as ncnn on the Pi? (checked on this computer)

Date: 2026-10-21. Phase 0 item from [the refactor plan](2026-10-21-refactor-plan.md): "Check on this computer that
Ultralytics exports `yolo26m-objv1-150` to ncnn and that it finds the same objects as the `.pt`".

Setup: Ultralytics 8.4.14 (same as the Pi), ncnn 1.0.20260526, pnnx 20260526, PyTorch 2.14.1, Apple M4 Max,
CPU only, `YOLO_AUTOINSTALL=false`. Model `yolo26m-objv1-150.pt` (365 kinds of objects) and confidence 0.35 from
`config/settings.yaml`.

## Short answer

- **The export works** at 640, 480 and 320 pixels, fp32 and fp16, in about 5 seconds each. No errors.
- **The conversion itself is faithful.** Run the same way, the ncnn model and the `.pt` find the same objects:
  98 to 100% of boxes match, boxes are 0.3 px apart in the middle case and under 2 px for 95% of them, and
  confidence moves by less than 0.01 on average. Every box one finds and the other doesn't is a weak one (under 0.45).
- **But it is not the same as the app's `.pt` today: about 91% of boxes match**, because Ultralytics runs an ncnn
  model differently in two ways:
  1. **The ncnn export leaves out YOLO26's end-to-end head.** YOLO26 has two heads: the end-to-end one the app uses
     now (no NMS), and a second one that needs NMS (removing overlapping boxes). ncnn can't do the end-to-end head's
     last step (picking the top boxes, "topk"), so Ultralytics 8.4.14 always exports the second head. The export says:
     `WARNING ⚠️ NCNN export does not support end2end models, disabling end2end branch.`
  2. **ncnn pictures are always padded to a square** (640 × 640). The `.pt` model pads only as much as it needs
     (a 1280 × 720 picture becomes 640 × 384). This alone changes about 9% of boxes, mostly weak ones.
- **Recommendation for the Pi: ncnn, fp16, 640 pixels first (480 if too slow).** To make the Pi and this computer
  agree, run this computer's backup `.pt` the same way (details at the end). ❓ needs your OK.

## 1. Export

`YOLO_AUTOINSTALL=false uv run yolo export model=models/yolo26m-objv1-150.pt format=ncnn imgsz=640` (add `half=True`
for fp16). It makes the folder `models/yolo26m-objv1-150_ncnn_model/` (now in `.gitignore`).

| picture size | precision | export time | size on disk |
|---|---|---|---|
| 640 | fp32 | 6.4 s | 83 MB |
| 640 | fp16 | 4.7 s | 41 MB |
| 480 | fp32 | 4.0 s | 83 MB |
| 480 | fp16 | 4.0 s | 41 MB |
| 320 | fp32 | 3.5 s | 83 MB |
| 320 | fp16 | 3.6 s | 41 MB |

All six worked. Important lines from the output (the same for every export):

```
WARNING ⚠️ NCNN export does not support end2end models, disabling end2end branch.
YOLO26m summary (fused): 156 layers, 22,307,622 parameters, 0 gradients, 69.4 GFLOPs
PyTorch: starting from 'models/yolo26m-objv1-150.pt' with input shape (1, 3, 640, 640) BCHW and output shape(s) (1, 369, 8400) (43.1 MB)
NCNN: starting export with NCNN 1.0.20260526 and PNNX 20260526...
.../torch/jit/_trace.py:1006: FutureWarning: `torch.jit.trace` is deprecated. Please switch to `torch.compile` or `torch.export`.
.../torch/jit/_trace.py:1145: FutureWarning: `torch.jit.trace_method` is deprecated. Please switch to `torch.compile` or `torch.export`.
NCNN: export success ✅ 4.4s, saved as 'models/yolo26m-objv1-150_ncnn_model' (78.8 MB)
```

The `torch.jit.trace` warning is harmless for now (pnnx still uses it). The exported model keeps all 365 object
names. As the benchmark found, `pnnx` must be installed and `YOLO_AUTOINSTALL=false` set, or Ultralytics tries
to pip-install things during the export.

## 2. Does it find the same objects?

**How:** 32 pictures (the first 30 COCO photos in `data/bench/datasets/coco128` plus Ultralytics' `bus.jpg` and
`zidane.jpg`), confidence 0.35, all on the CPU. A box counts as matched when the other model has a box with the same
label that overlaps it well (IoU at least 0.5). "Strong" = confidence 0.45 or more, the level at which an agent
counts an object as seen (`agents.min_confidence`). "Box gap" = the largest difference of any box corner, in picture
pixels (the COCO photos are about 640 × 480).

**ncnn compared with the `.pt` run the ncnn way** (no end-to-end head, square padding): this checks the conversion.

| size | ncnn | boxes matched | missed (strong) | extra (strong) | box gap middle / 95% / largest | confidence change average / largest |
|---|---|---|---|---|---|---|
| 640 | fp32 | 181 of 182 (99.5%) | 1 (0) | 4 (0) | 0.3 / 1.8 / 39.6 px | 0.006 / 0.05 |
| 640 | fp16 | 178 of 182 (97.8%) | 4 (0) | 2 (0) | 0.3 / 1.6 / 9.2 px | 0.007 / 0.05 |
| 480 | fp32 | 173 of 175 (98.9%) | 2 (0) | 0 (0) | 0.3 / 1.6 / 9.0 px | 0.007 / 0.04 |
| 480 | fp16 | 173 of 175 (98.9%) | 2 (0) | 1 (0) | 0.3 / 1.8 / 3.7 px | 0.008 / 0.04 |
| 320 | fp32 | 138 of 138 (100%) | 0 (0) | 3 (0) | 0.4 / 2.0 / 13.5 px | 0.008 / 0.10 |
| 320 | fp16 | 137 of 138 (99.3%) | 1 (0) | 4 (1) | 0.5 / 1.9 / 3.6 px | 0.008 / 0.14 |

The few large gaps are single weak boxes where two overlapping boxes had nearly the same score and NMS kept a
different one (the 39.6 px one is a car at 0.47; only 4 of the 181 matched boxes at 640 fp32 differ by more than 3 px).

**ncnn compared with the app's `.pt` as it runs today** (end-to-end head, tight padding):

| size | ncnn | boxes matched | missed (strong) | extra (strong) | box gap middle / 95% / largest |
|---|---|---|---|---|---|
| 640 | fp32 | 161 of 174 (92.5%) | 13 (3) | 24 (13) | 0.6 / 5.5 / 29.0 px |
| 640 | fp16 | 158 of 174 (90.8%) | 16 (5) | 22 (11) | 0.6 / 4.7 / 40.5 px |
| 480 | fp32 | 153 of 168 (91.1%) | 15 (5) | 20 (10) | 0.8 / 8.3 / 48.4 px |
| 480 | fp16 | 154 of 168 (91.7%) | 14 (4) | 20 (10) | 0.8 / 9.0 / 50.5 px |
| 320 | fp32 | 116 of 132 (87.9%) | 16 (7) | 25 (13) | 0.9 / 5.5 / 33.2 px |
| 320 | fp16 | 116 of 132 (87.9%) | 16 (6) | 25 (12) | 0.8 / 6.7 / 32.7 px |

**Why the difference** (640 pixels, `.pt` only, no ncnn involved):

| change from the app's `.pt` run | boxes matched | missed (strong) | extra (strong) |
|---|---|---|---|
| square padding only | 159 of 174 (91.4%) | 15 (4) | 8 (3) |
| other head only (both padded square) | 158 of 167 (94.6%) | 9 (1) | 24 (13) |
| both (= what ncnn does) | 158 of 174 (90.8%) | 16 (5) | 24 (13) |

So the ncnn conversion adds almost nothing; the gap comes from how Ultralytics runs the model. The second head finds
more boxes. Some are clearly real: in one photo it finds a plate at 0.91 that today's `.pt` misses completely. Others
may be doubles (two "boots", two "clock"). With no labelled Objects365 photos here, I can't say which head is more
accurate overall.

**Experiment: keeping the end-to-end head in ncnn.** Copying the end-to-end head's weights into the second head's
place before exporting (no Ultralytics code changed, only the loaded model) gives an ncnn model that behaves like
the end-to-end head plus NMS:

```python
head = model.model.model[-1]                     # model = YOLO('models/yolo26m-objv1-150.pt')
head.cv2, head.cv3 = copy.deepcopy(head.one2one_cv2), copy.deepcopy(head.one2one_cv3)
```

Against the app's `.pt` with square padding it matches 97.6% at 640 (box gap 95% under 1.1 px, 0 to 1 strong extras),
and only 5 extra boxes against today's `.pt` (instead of 24). But it reaches into Ultralytics' insides (`cv2`,
`one2one_cv2` ...), so an Ultralytics update could quietly break it, and it is a little worse at 480 and 320
(96% and 92 to 94%). The proper fix would be in Ultralytics itself (export the end-to-end head without its last
"topk" step and do that step in Python); 8.4.14 doesn't do this.

## 3. Speed on this Mac (CPU, a rough hint only)

Middle (median) time for one 1280 × 720 picture (`zidane.jpg`, the Pi's picture size), the whole Ultralytics call
(resize, model, boxes). 25 rounds after 4 warm-ups, every model once per round in a shuffled order. PyTorch used 8
threads (Ultralytics' default), ncnn its own default (10). The Mac was busy with other work during the run
(load average about 22), so these are rough.

| picture size | `.pt` | ncnn fp32 | ncnn fp16 |
|---|---|---|---|
| 320 | 32 ms | 22 ms | 21 ms |
| 480 | 46 ms | 42 ms | 41 ms |
| 640 | 61 ms | 71 ms | 71 ms |

On the Mac, ncnn is no faster than PyTorch. The benchmark's laptop run showed the same for yolo11m (640: `.pt` 56 ms,
ncnn 79 ms). PyTorch is very well tuned for Apple chips; the Pi's CPU is very different and Ultralytics recommends
ncnn there. Only the Pi run of the benchmark can tell.

fp32 and fp16 run at the same speed here because ncnn already does its math in fp16 on CPUs that support it (this
Mac does; the Pi 5's CPU should too) whichever file you give it; the fp16 file just stores the weights in half the
space. On a Pi 4 (no fp16 math) both files should run in fp32.

## 4. Recommendation for the Pi

- **Format: ncnn.** The export works and the conversion is faithful.
- **fp16** (`half=True`): half the size (41 MB instead of 83 MB, so less memory on the Pi), with the same match.
- **Picture size: 640 first**, the size the app uses now. If the Pi is too slow, **480 next**: on these photos the
  `.pt` found 168 boxes at 480 against 174 at 640 (but only 132 at 320), with a bit over half the work. Leave 320 for
  last.
- ❓ **Make both places run the model the same way.** Otherwise the Pi (ncnn) and this computer (`.pt` backup, phone,
  webcam) will disagree on about 1 box in 10. Choices:
  1. *(recommended)* Plain Ultralytics on both: this computer runs the `.pt` with `end2end=False, rect=False`
     (two new settings under `detector:`). Then both find the same objects (first table above). It's a small
     change in what the app sees today: about 13 more strong boxes over 32 photos, some of them real objects
     the end-to-end head misses.
  2. Keep the end-to-end head: the Pi's detector copies the head weights before exporting (the experiment above),
     and this computer runs the `.pt` with `rect=False`. Closer to today's results, but depends on Ultralytics'
     insides.
- The detector container should set `YOLO_AUTOINSTALL=false` and have `pnnx` installed when it exports on first start.

## How to check it again

`tests/slow/test_ncnn_matches_pt.py` loads the `.pt` and `models/<model name>_ncnn_model/` (model, confidence and
picture size from `config/settings.yaml`) and checks, on `bus.jpg` and `zidane.jpg` (they come with Ultralytics),
that both find the same labels with boxes within 3 pixels at the model's picture size. Boxes within 0.1 of the
confidence threshold may come and go. It runs the `.pt` the ncnn way (see above), and is skipped if either model is
missing.

```
uv run python scripts/download_models.py
YOLO_AUTOINSTALL=false uv run yolo export model=models/yolo26m-objv1-150.pt format=ncnn imgsz=640
uv run pytest -m slow tests/slow
```

It passes with the fp32 and the fp16 export. It fails with a 320-pixel export used at 640 (boxes off by 7 to 39 px,
people missing), and when the allowed gap is cut to 0.05 px (real gaps are 0.1 to 4.1 px).
