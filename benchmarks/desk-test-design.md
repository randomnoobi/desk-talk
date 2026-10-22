# Desk test for the YOLO benchmark: design

Status: draft, waiting for Sam's approval. Written 2026-10-21.

## Why

The benchmark in `benchmarks/` (deskbench) timed yolo11n/s/m on the laptop and the
Pi and picked **one** "best" model. Its accuracy score came from 128 general COCO
photos (coco128), not desks. Two problems with that pick:

- yolo11 knows 80 kinds of objects. It can't see two of the eight things on Sam's
  desk at all: AirPods and trash.
- The app itself doesn't use yolo11. `config/settings.yaml` runs
  `yolo26m-objv1-150` (Objects365, 365 kinds, including earphones). That model was
  never timed on the Pi.

Sam wants a desk test: desk pictures with the kinds of things on their desk, including
hard ones where things cover each other. Every model is scored on those, and they
get **three** good models that are also fast on the Pi, so they can try them on their
real desk.

## Sam's desk

The eight kinds of things the test scores, in Sam's words:

| Kind | Notes |
|---|---|
| book | often stacked |
| water bottle | plastic bottles, sometimes two |
| mouse | often under their hand |
| laptop | always there, usually with their arm and hand over it |
| food | "maybe some food" |
| AirPods | the white case, and the earbuds out of it |
| phone | on the desk, on the laptop, in their hand, face down |
| trash | "this is hard to detect": crumpled paper, wrappers, empty cans, tissues |

Their arm or hand is in nearly every picture. That's normal for the app, so it stays
in the test pictures. The person isn't one of the eight scored kinds (my call: the
app finds people easily, and Sam didn't list it).

## Goals

1. Score every model below on desk pictures, for each of the eight kinds and each
   scene (tidy, busy, covering, small things, food, trash, Sam's desk).
2. Time the new models on the Pi, so speed and desk score can be judged together.
3. Name three different models that are fast enough on the Pi and score best on
   the desk, and say for each what it's good and bad at and how to try it in the app.

## Not goals

- Changing which model the app uses. Sam tries the three picks themself.
- Training or fine-tuning a model on their desk.
- Running the accuracy part on the Pi. A model file gives the same answers on any
  computer, so accuracy runs on the Mac only. Only speed is measured on the Pi.
- Box-position accuracy (mAP). The habit rules only ask "is it on the desk?", so the
  desk test scores whether each kind was found, not how tight the box is (my call).

## Must not happen (anti-goals)

- Sam's desk photos or anything showing their room ending up in git. The repo is on
  GitHub and deploys to GitHub Pages. Their photos stay in `data/bench/`, which isn't
  in git.
- Picks that look good overall but can't see their things. A kind a model has no name
  for (yolo11 and AirPods, say) scores 0 for that kind. It is never left out of the
  average.
- Hiding weak spots. If every model is bad at trash, the report says so in words.
- COCO-trained models scoring high on photos they were trained on (see Pictures).

## Models

Four kinds, each in three sizes (n, s, m), each at picture sizes 320, 480 and 640.
That's 12 models and 36 model and size pairs. Ultralytics 8.4.14 (in
`benchmarks/uv.lock`) can download all of them. I checked that each file exists on
the Ultralytics GitHub release.

| Kind | Files | Knows |
|---|---|---|
| yolo11 | `yolo11{n,s,m}.pt` | 80 COCO kinds (already timed) |
| yolo26 | `yolo26{n,s,m}.pt` | 80 COCO kinds, newer |
| yolo26-objv1 | `yolo26{n,s,m}-objv1-150.pt` | 365 Objects365 kinds (the app's kind) |
| yoloe-26 | `yoloe-26{n,s,m}-seg.pt` | whatever it's told in words (text prompts) |

**Formats.** yolo11 keeps all five from last time (pt fp32, ncnn fp32 and fp16,
onnx fp32 and int8). The new nine models only get ncnn fp32 and ncnn fp16, the
fastest on the Pi last time (my call). So there are 54 new combinations to time on
the Pi (9 models × 3 sizes × 2), about the same as last run's 45.

**yoloe-26.** It is told what to look for in words. The words are fixed before the
model is converted to ncnn, so the converted model only knows those words. The words
go in config, not code.

**Its words are the app's own names** (my call). The app matches detector names
exactly against the `labels` lists in `config/agents.yaml`
(`desktalk/agents/profiles.py`, `for_label`). So the yoloe words for the first seven
kinds are taken from those lists:

- `bottle` and `water bottle` from the cup agent
- `cell phone` from the phone agent
- `earphone` and `earbuds` from the earbuds agent, plus `head phone` and `headphones`
  from the headphones agent. Added after the first run: most public "AirPods" photos
  show headphones, which the 365-kind models get to name ("Head Phone"), so yoloe
  gets them too.
- `laptop` and `mouse` from the computer agent
- `book` from the paper agent
- the foods from the snack agent

That way a yoloe pick drops into the app with no change to `agents.yaml`, and the
habit rules (phone-free time, hydration) keep working. The builder may check
whether a clearer word ("computer mouse") finds more. But the pick that ships uses
the app's names, or the report says exactly which `agents.yaml` line Sam would need
to add.

Trash has no names in the app: there's no trash agent. The yoloe trash words are
crumpled paper, snack wrapper, empty can and used tissue. The report says that in
the app, trash found this way would show up as new, unknown objects.

Other things often on desks are added too, so it has somewhere to put them instead
of calling everything a phone: person, keyboard, cup, tissue box, pen, cable. The
builder may tune the trash and extra words on the public pictures, but not on
Sam's six.

Two risks I haven't checked: whether yoloe-26 converts to ncnn cleanly, and how
much its mask part slows it down. Only `-seg` versions exist, so its speed includes
working out the shape of each object. If it won't convert to ncnn, time it as onnx
fp32 instead and say so in the report.

## Mapping each model's names to Sam's eight kinds

Each kind of model names things differently. A table in config maps names to the
eight kinds (config, not code, per AGENTS.md). A starting point, checked against
the name lists in Ultralytics' `coco.yaml`, `Objects365.yaml` and `lvis.yaml`:

| Kind | COCO (yolo11, yolo26) | Objects365 (yolo26-objv1) | LVIS (the right answers) |
|---|---|---|---|
| book | book | Book | book, paperback book, booklet |
| water bottle | bottle | Bottle | bottle, water bottle |
| mouse | mouse | Mouse | mouse (computer equipment) |
| laptop | laptop | Laptop | laptop computer |
| food | banana, apple, sandwich, orange, broccoli, carrot, hot dog, pizza, donut, cake | Bread, Apple, Cake, Banana, Pizza, Donut, Cookies, Candy, Hamburger, Sandwich, Chips, and the other foods | apple, banana, bagel, cake, cookie, candy bar, potato chip, pizza, sandwich, doughnut, cupcake, and the other foods |
| AirPods | (none) | earphone, Head Phone | earphone, headset |
| phone | cell phone | Cell Phone | cellular telephone |
| trash | (none) | Tissue, Napkin, Canned | garbage, can, tissue paper, napkin, paper towel, receipt, bottle cap, straw |

The trash row is a judgment call: no list has "trash", only pieces of it. The report
says that next to the trash numbers. The full lists go in config, where Sam can read
and change them.

## Pictures

### 1. Public desk photos (the main set)

From **LVIS v1 val** (`lvis_v1_val.json.zip`, 64 MB, from dl.fbaipublicfiles.com).
LVIS labels COCO photos with 1,203 kinds of objects, including earphones and some
trash.

- **Only photos from COCO val2017.** Their `coco_url` contains `val2017`. The COCO
  models were trained on COCO train2017, and LVIS val also contains train2017 photos,
  which would flatter yolo11 and yolo26.
- **Desk photos only.** At least two of the eight kinds are labeled in the photo, and
  at least one of them is a laptop, mouse, book, phone or AirPods. Take all photos
  that pass, up to about 300. If more pass, keep them all for the rare kinds
  (AirPods, trash, food) and sample the rest at random with a fixed seed.
- Photos are downloaded one by one from `coco_url` into `data/bench/desk/lvis/` on
  first use (about 50 MB), not the 20 GB LVIS zip. The list of chosen photo IDs, with
  their scenes and right answers, is kept **in git**, so every run uses exactly the
  same pictures and nobody needs the 64 MB label file again.

**The right answers come from two label sets on the same photos:**

- **COCO's own val2017 labels.** They are complete for COCO's 80 kinds: if a book was
  in the photo, it's labeled. Ultralytics' `coco2017labels` download or COCO's
  `instances_val2017.json` both work (builder's call).
- **LVIS labels.** They cover far more kinds but aren't complete. For each photo,
  LVIS lists the kinds that are there, some kinds checked and not there
  (`neg_category_ids`), and kinds that are there but not every one is boxed
  (`not_exhaustive_category_ids`).

For each photo and each of the eight kinds the right answer is (my call, after the
reviewer pointed out that LVIS alone would give almost no "not there" photos):

- **there**: a matching name is labeled in COCO or LVIS
- **not there**: no matching name is labeled in either, and LVIS doesn't flag any of
  that kind's names as `not_exhaustive` for the photo
- **unknown**: anything else. It isn't scored for that kind.

For book, water bottle, mouse, laptop and phone, "not there" is solid, because
COCO labels them completely. For AirPods, trash and foods COCO doesn't know,
there's a small chance an unlabeled one is in a photo, and a model that finds it
would be marked wrong. That risk is accepted, and the report says so under those
kinds.

**Scenes.** Worked out from the labels. A photo can be in more than one scene. The
thresholds below are my call, and the builder may adjust them so each scene gets a
fair number of photos:

| Scene | Rule |
|---|---|
| covering | one of the eight kinds has at least 30% of its box covered by another labeled object's box |
| busy | 6 or more labeled objects of any kind |
| small things | AirPods there, or a phone or mouse whose box is under 1% of the photo |
| food | food there |
| trash | trash there |
| tidy | none of the above |

### 2. Sam's desk (their own six)

Taken with the Pi camera from its usual spot (1280×720), saved in
`data/bench/desk-photos/desk_01.jpg` … `desk_06.jpg`. **Not in git.** Their right
answers go in `data/bench/desk-photos/labels.yaml`, next to them, also not in git.
Every one of the eight kinds not listed for a picture counts as **not there**.

| # | There | Notes |
|---|---|---|
| 1 | laptop | the black square top right is part of the desk, not a phone |
| 2 | laptop, mouse | a shiny black edge in the top right corner is the same desk square |
| 3 | laptop, mouse, water bottle, phone | bottle half out of the picture; phone blurry |
| 4 | laptop, book, phone, water bottle, mouse | tissue box (not trash); a bit blurry |
| 5 | laptop, water bottle, mouse, phone, book | book half under their arm; tissue box |
| 6 | laptop, water bottle, book, mouse, phone, AirPods | hand over the mouse and the phone; books stacked; a second green drink bottle; tissue box |

The laptop screen in these pictures shows the camera's live view, with another laptop
and hand on it. Scoring "found or not" per kind makes this harmless: finding the
laptop twice is still just "found".

The second green bottle in #6 is a water bottle for scoring purposes (it's a bottle).
The tissue box isn't trash and isn't one of the eight kinds.

Six pictures are too few to rank 36 models closely. They're a check that a pick
doesn't fall apart on Sam's real desk, not the main ranking. Sam can add more later
the same way.

## Scoring

For each combination, picture and kind: the model **found** the kind if it gave any
box with a mapped name at confidence ≥ the threshold.

**The threshold is the app's own rule** (my call, so scores match what the app will
do on Sam's desk). That's `agents.min_confidence` in `config/settings.yaml`
(0.45 today). A kind whose app agent sets its own `min_confidence` in
`config/agents.yaml` uses that instead, e.g. the cup agent's 0.55 for bottles. Both
are read from the app's config, not copied, and the threshold is the same for
every model.

Some models (the 365-kind ones, yoloe) tend to be less sure of themselves, which
this may hold back. So each combination's detections are saved down to confidence
0.25, and the report also shows the desk score at 0.25. A model that's good but
held back by the app's threshold is then visible. The picks still use the app's
rule.

For each kind:

- **found rate**: share of the pictures where it's *there* that the model found it
- **false alarms**: share of the pictures where it's *not there* that the model said
  it was

A kind's **score** = found rate × (1 − false alarms). The **desk score** = the
average of the eight kinds' scores on the public photos, each kind counting the same.
A kind the model has no name for scores 0.

The same numbers are also worked out for each scene, and separately for Sam's six.

If a kind has fewer than 15 public photos where it's there (likely for AirPods and
trash), the report shows how many there were, so a 100% from 3 photos isn't
mistaken for a sure thing.

**When there's nothing to count** (my call):

- A kind with **no "there" photos** at all shows as "no photos" and is left out of the
  desk score for every model alike. This is different from a model having no name for
  a kind, which still scores 0.
- A kind with **no "not there" photos** scores its found rate alone, with a note.
- A scene × kind cell with fewer than 5 photos shows "–".

## Speed

The 54 new combinations are timed on the Pi with the existing deskbench run, using
the same settings, the same Docker image, cooling between combinations and
`--resume`. That also means a second Pi run of a few hours. yolo11's Pi speed comes
from the run already in `benchmarks/results/pi/`.

The Mac can time them too (as last time), but picks only use the Pi's numbers.

**Two Pi runs, one picture** (my call, after the reviewer pointed out that
`report.py` only shows each device's latest run):

- For each device, the report and the picks use the latest ok row of each
  combination across *all* that device's runs. yolo11 (first run) and the new models
  (second run) then show up side by side.
- The run history table still lists each run on its own.

**The old COCO accuracy check (coco128 mAP)** only means something for models that
use COCO's 80 kinds. It stays on for yolo26, but is turned off for yolo26-objv1 and
yoloe-26, whose kind numbers don't line up with COCO's. That also shortens the Pi
run. The per-device "best pick" keeps its current rule. Models without a coco128
score just aren't in it, which is fine, because the three picks are the new answer.

## The three picks

1. Keep the combinations whose **Pi** total p95 is ≤ 333 ms, the same rule as the
   current best pick (`report.camera_fps: 3`).
2. For each model (kind and size, e.g. yolo26s-objv1), keep its fastest-enough
   combination with the highest desk score.
3. The three models with the highest desk scores are the picks. They are always
   three different models.

For each pick the report shows:

- its desk score and its score for each kind and scene
- its score on Sam's six, flagged if clearly worse than on the public photos
  (wording and exact rule are the builder's call)
- its Pi speed
- a line of plain words on what it's good and bad at
- how to try it in the app: which model file and picture size to put in
  `config/settings.local.yaml` (Sam edits that file themself; agents never do)

Note on the app: today the app detects on the Mac, not the Pi. So "try it on the
desk" means running the app on the Mac with that model. Whether detection moves to
the Pi is outside this work. For yoloe-26, the pick must come with a model file that
already has the words built in, so the app can load it like any other model. The
builder works out how to save that.

## Where things go

- Settings: a new `desk:` section in `config/benchmark.yaml` covering the kinds and
  their name lists per model kind, the yoloe words, the threshold, the scene rules,
  the photo count and seed, and the picture folders. The model list grows to the
  12 models, with formats chosen per kind.
- Results: desk scores in `benchmarks/results/desk/<run>.csv` and `.json` (in git,
  like the speed results): one row per combination, with columns per kind and scene.
- Report: `benchmarks/results/README.md` gets a "Desk test" section and a
  "Three picks to try" section above the per-device tables.
- The current single "best pick" stays per device with its current rule, now over
  all of that device's runs (see Speed).
- Docs: `benchmarks/README.md` explains the desk test, how to run it, and how to add
  more desk photos of your own.

## Tests

The usual rules: no network, no camera, no Pi, no real models (AGENTS.md). Cover:

- choosing photos from LVIS-style labels: val2017 only, desk rule, rare kinds kept,
  fixed seed
- the three right answers (there, not there, unknown) from COCO plus LVIS labels,
  especially that unknown is never scored and `not_exhaustive` makes a kind unknown
- the threshold read from the app's settings and agents files, with per-agent
  overrides
- empty cases: no "there" photos, no "not there" photos, tiny scene cells
- the report merging two runs of the same device
- scene rules, including the covering rule
- name mapping, including a kind the model has no name for scoring 0
- scores and desk score on a small made-up set
- the three-picks rule: the speed limit, one combination per model, three
  different models, fewer than three when fewer qualify
- Sam's labels file being read correctly

## Left to the builder

- The command shape (e.g. `deskbench desk prepare`, `deskbench desk run`) and how it
  fits the existing `run_one` process-per-combination setup.
- Exact scene thresholds and the yoloe words, within the rules above.
- How to save a yoloe-26 model with its words built in.
- How the report words the "worse on your desk" flag.

## Risks

- yoloe-26's training data. Checked: the model file names its training sets, and
  the grounding set is `final_mixed_train_no_coco`, so COCO's photos were taken out.
  The val2017 photos are new to it.
- yoloe-26 converts to ncnn (fp32 and fp16) once its words are saved into a `.pt`
  (checked in a throwaway test). Its mask part may still make it too slow for the
  Pi. Either way it's reported, not hidden.
- LVIS has few photos with earphones or trash in val2017. The scores for those two
  kinds may rest on a handful of photos, and the report shows the counts.
- Sam's six are all from one evening, one angle and one light. Lighting scenes were
  dropped. The report says that.
