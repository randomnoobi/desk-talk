# Desk test: build plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score 12 YOLO models (4 kinds × n/s/m) at 320/480/640 on desk photos for Sam's eight kinds of things, time the new ones on the Pi, and name three fast, accurate models to try on the desk.

**Architecture:** Extends `benchmarks/deskbench`. Model kinds move into config, each with its own formats and names. A new desk photo set (public LVIS/COCO val2017 photos plus Sam's own six) is chosen once and kept in git as `benchmarks/desk/desk-set.json`. `deskbench desk run` runs each combination in its own process (like `run_one`), saves its detections, and scores them in the parent with pure functions. The report merges each device's runs and adds a desk section and the three picks.

**Tech Stack:** Python 3.12, Ultralytics 8.4.14, ncnn/onnx, pytest, uv. yoloe words need `clip` from git (Mac only, new `words` dependency group).

**Spec:** [benchmarks/desk-test-design.md](desk-test-design.md). Read it first. This plan argues from it.

## Global Constraints

- AGENTS.md rules apply: plain words everywhere ("pictures", "the Pi"). Numbers, names, paths and thresholds go in `config/benchmark.yaml`, not code. Never edit `config/settings.local.yaml`.
- Tests never need the network, a camera, a Pi or a real model. Run them with `cd benchmarks && uv run --group dev pytest`. Lint only changed files with `uv run --group dev ruff check <files>`. Don't run `ruff format`.
- Sam's photos and anything made from them (`data/bench/desk-photos/`) never go in git. `benchmarks/desk/desk-set.json` holds public photo IDs and answers only.
- Don't commit. Work stays uncommitted on branch `desk-test` until Sam asks for a commit.
- The existing results must keep working: combination keys stay `<model>-<size>-<format>-<precision>`, and `--resume` on an old CSV still works.
- yoloe: the words are fixed into a `.pt` with `YOLOE(base).set_classes(words, m.get_text_pe(words))` then `.save()`. **Each export of a yoloe model runs in its own process.** A second export in the same process fails with `TypeError: 'NoneType' object is not iterable` in `head.py _fuse_tp` (seen in a spike). Load the words model with `YOLOE(path)` for export, and with `task='segment'` for prediction.
- The app loads only `.pt` files (`desktalk/vision/detector.py resolve_model_path` treats a relative path from the project root as a file). "Try it in the app" lines therefore name `models/bench/<model>.pt` (yoloe: `models/bench/<model>-desk.pt`) plus `image_size`.

## Review Focus

1. **Changed yoloe words after the models were made.** Expect the old yoloe files to be remade, not silently reused. Test in Task 2 (`test_changed_words_remove_old_yoloe_files`).
2. **A desk photo missing on disk** (download failed, or another computer). Expect `desk run` to skip it with a warning, record how many pictures it used, and not crash. Test in Task 5 (`test_missing_picture_is_skipped_and_counted`).
3. **Sam's `labels.yaml` missing or naming an unknown kind.** Missing: the run goes on without "your desk", `your_desk_score` stays empty, and the report says so. Unknown kind: a clear error naming it. Tests in Task 3 (`test_your_desk_labels_*`).
4. **Model names in a different case** ("Cell Phone" vs "cell phone"). Expect a match. Test in Task 4 (`test_names_match_ignoring_case`).
5. **The first Pi CSV, made with the old config shape, merged with the new run.** Expect both runs' combinations side by side, newest row winning for the same combination. Test in Task 6 (`test_report_merges_runs_of_a_device`).

---

### Task 1: Model kinds in config

**Files:**
- Modify: `config/benchmark.yaml`. Replace the top-level `models:` and `formats:` with `model_kinds:` (below). Keep `image_sizes`.
- Modify: `benchmarks/deskbench/matrix.py`, `benchmarks/deskbench/cli.py`, `benchmarks/deskbench/run_one.py`
- Test: `benchmarks/tests/test_matrix_and_stats.py`

```yaml
model_kinds:            # each kind: its models, which names it uses, and the formats to try
  yolo11:       {models: [yolo11n, yolo11s, yolo11m], names: coco,
                 formats: {pt: [fp32], ncnn: [fp32, fp16], onnx: [fp32, int8]}}
  yolo26:       {models: [yolo26n, yolo26s, yolo26m], names: coco, formats: {ncnn: [fp32, fp16]}}
  yolo26-objv1: {models: [yolo26n-objv1-150, yolo26s-objv1-150, yolo26m-objv1-150], names: objects365,
                 formats: {ncnn: [fp32, fp16]}}
  yoloe-26:     {models: [yoloe-26n-seg, yoloe-26s-seg, yoloe-26m-seg], names: words, task: segment,
                 formats: {ncnn: [fp32, fp16]}}
```

Write it in the file's existing commented style, with a plain comment on each key: `names` is coco, objects365 or words, and `task` defaults to detect.

**Interfaces:**
- Produces:
  - `matrix.kind_of(cfg: dict, model: str) -> tuple[str, dict]`: returns (kind name, kind settings). Raises `KeyError(f'{model} is not in model_kinds')`.
  - `matrix.build_combos(cfg: dict, only_models: list[str] | None = None, only_kinds: list[str] | None = None) -> list[Combo]`: order is kinds in config order, then models, sizes, formats, precisions.
  - The `run_one` spec gains `'task': str`.
  - CLI `run` and `prepare` gain `--kinds`.

- [ ] **Step 1: Update the tests in `test_matrix_and_stats.py`.** `CFG` becomes a `model_kinds` dict with two kinds:

```python
CFG = {'image_sizes': [320, 640], 'model_kinds': {
    'a': {'models': ['yolo11n', 'yolo11s'], 'names': 'coco',
          'formats': {'pt': ['fp32'], 'ncnn': ['fp32', 'fp16'], 'onnx': ['fp32', 'int8']}},
    'b': {'models': ['yoloe-26n-seg'], 'names': 'words', 'task': 'segment', 'formats': {'ncnn': ['fp16']}}}}

def test_every_combination_is_listed_once_in_order():
    combos = build_combos(CFG)
    assert len(combos) == 2 * 2 * 5 + 1 * 2 * 1
    assert combos[0] == Combo('yolo11n', 320, 'pt', 'fp32')
    assert combos[-1] == Combo('yoloe-26n-seg', 640, 'ncnn', 'fp16')

def test_each_kind_gets_only_its_own_formats():
    assert {c.fmt for c in build_combos(CFG, only_kinds=['b'])} == {'ncnn'}

def test_kind_of_a_model():
    assert kind_of(CFG, 'yoloe-26n-seg')[0] == 'b'
    with pytest.raises(KeyError):
        kind_of(CFG, 'yolo99x')
```

Keep `test_only_some_models` and the bad-precision test, now applied inside one kind.

- [ ] **Step 2: Run them.** `cd benchmarks && uv run --group dev pytest tests/test_matrix_and_stats.py`. Expected: FAIL (`kind_of` doesn't exist).
- [ ] **Step 3: Implement `kind_of` and the new `build_combos`.** Validate every kind's formats as today. Unknown `names` value → `ValueError` listing coco, objects365, words.
- [ ] **Step 4: Wire it up.**
  - `cli.make_run_combo` puts `'task': kind.get('task', 'detect')` in the spec.
  - It sets `accuracy` to `None` unless `kind['names'] == 'coco'` (the spec: coco128 only means something for COCO kinds).
  - `run_one.run` uses `YOLO(spec['model_path'], task=spec['task'])`.
  - `cmd_run` and `cmd_prepare` pass `--kinds` through.
  - `probes.environment`/info JSON need no change.
- [ ] **Step 5: Run all tests.** `uv run --group dev pytest`. Expected: all pass. Then `uv run --group dev ruff check deskbench/matrix.py deskbench/cli.py deskbench/run_one.py tests/test_matrix_and_stats.py`. Expected: clean.

### Task 2: yoloe models with Sam's words built in

**Files:**
- Modify: `benchmarks/pyproject.toml`: add `words = ["clip @ git+https://github.com/ultralytics/CLIP.git"]` under `[dependency-groups]`, then `uv lock`.
- Create: `benchmarks/deskbench/words.py` (pure helpers + a `python -m deskbench.words` entry that runs in its own process)
- Modify: `benchmarks/deskbench/models.py`, `benchmarks/deskbench/cli.py`
- Test: `benchmarks/tests/test_words.py`

**Interfaces:**
- Consumes: `kind_of` (Task 1). Desk config `desk.kinds.<kind>.words` and `desk.yoloe_extra_words` (Task 3 adds them to config. This task reads them through `desk_words`).
- Produces:
  - `words.desk_words(desk_cfg: dict) -> list[str]`: every kind's `words` in config order, then `yoloe_extra_words`, without repeats, lowercased.
  - `words.words_model_path(models_dir: Path, model: str) -> Path` → `models_dir / f'{model}-desk.pt'`
  - `words.stamp_path(models_dir: Path, model: str) -> Path` → `models_dir / f'{model}-desk.words.json'`
  - `words.clear_if_changed(models_dir: Path, model: str, words: list[str]) -> bool`: if the stamp is missing or differs, delete `<model>-desk.pt`, the stamp, every `<model>_*_ncnn_model` folder and `<model>_*.onnx` file, and return True.
  - `models.ensure_model(combo, models_dir, log=print, words: list[str] | None = None) -> Path`: with `words`, the "pt" for that model is the words `.pt`, made by `python -m deskbench.words make <base.pt> <words.json> <target>`. Each ncnn/onnx export goes through `python -m deskbench.words export <words.pt> <fmt> <imgsz> <half 0|1> <target>`. Both use `subprocess.run(..., check=True)`.
  - Without the `clip` package, `make` fails with: `yoloe needs its words made on the Mac: cd benchmarks && uv sync --group words && uv run deskbench prepare --kinds yoloe-26, then copy models/bench to the Pi`.

- [ ] **Step 1: Write `tests/test_words.py`.**

```python
def test_desk_words_keep_order_and_drop_repeats():
    cfg = {'kinds': {'phone': {'words': ['cell phone']}, 'trash': {'words': ['empty can', 'Cell Phone']}},
           'yoloe_extra_words': ['person', 'empty can']}
    assert desk_words(cfg) == ['cell phone', 'empty can', 'person']

def test_changed_words_remove_old_yoloe_files(tmp_path):
    m = 'yoloe-26n-seg'
    (tmp_path / f'{m}-desk.pt').write_text('x')
    (tmp_path / f'{m}_320_fp16_ncnn_model').mkdir()
    (tmp_path / 'yolo11n_320_fp16_ncnn_model').mkdir()            # another model: left alone
    stamp_path(tmp_path, m).write_text(json.dumps(['old']))
    assert clear_if_changed(tmp_path, m, ['new']) is True
    assert not (tmp_path / f'{m}-desk.pt').exists() and not (tmp_path / f'{m}_320_fp16_ncnn_model').exists()
    assert (tmp_path / 'yolo11n_320_fp16_ncnn_model').exists()

def test_same_words_keep_files(tmp_path):
    m = 'yoloe-26n-seg'
    (tmp_path / f'{m}-desk.pt').write_text('x')
    stamp_path(tmp_path, m).write_text(json.dumps(['a', 'b']))
    assert clear_if_changed(tmp_path, m, ['a', 'b']) is False
    assert (tmp_path / f'{m}-desk.pt').exists()
```

- [ ] **Step 2: Run.** Expected: FAIL (no module `deskbench.words`).
- [ ] **Step 3: Implement `words.py` and the `ensure_model` change.**
  - `make` writes the stamp after saving the `.pt`. Exports move the result to the target the same way `ensure_model` does today: a temporary name, then rename.
  - In `cli.make_run_combo` and `cmd_prepare`, pass `words=desk_words(cfg['desk'])` when `kind['names'] == 'words'`, and call `clear_if_changed` once per words model before the first combination.
- [ ] **Step 4: Run the tests and ruff on the changed files.** Expected: pass, clean.
- [ ] **Step 5: Check it for real on the Mac.** `cd benchmarks && uv sync --group words && uv run deskbench prepare --models yoloe-26n-seg`. Expected:
  - `models/bench/yoloe-26n-seg-desk.pt`, its stamp, and three sizes × two ncnn folders exist.
  - `python -c "from ultralytics import YOLO; print(YOLO('../models/bench/yoloe-26n-seg_480_fp16_ncnn_model', task='segment').names)"` prints the words.

### Task 3: Desk photo set and right answers

**Files:**
- Modify: `config/benchmark.yaml`: add a `desk:` section (below)
- Create: `benchmarks/deskbench/desk_set.py` (pure), `benchmarks/deskbench/desk_data.py` (downloads; not unit-tested)
- Create: `benchmarks/desk/desk-set.json` (made by `deskbench desk prepare`, in git)
- Create: `data/bench/desk-photos/labels.yaml` (Sam's six, from the spec's table; not in git)
- Test: `benchmarks/tests/test_desk_set.py`

Desk config (values from the spec; write the LVIS names from the `name` field of `lvis_v1_val.json` categories, which uses underscores, e.g. `laptop_computer`; check every one exists when building):

```yaml
desk:
  kinds:                     # Sam's eight kinds of things, and what each set of names calls them
    book:         {coco: [book], objects365: [Book], words: [book], app_agent: paper,
                   lvis: [book, paperback_book, booklet]}
    water bottle: {coco: [bottle], objects365: [Bottle], words: [bottle, water bottle], app_agent: cup,
                   lvis: [bottle, water_bottle]}
    mouse:        {coco: [mouse], objects365: [Mouse], words: [mouse], app_agent: computer,
                   lvis: [mouse_(computer_equipment)]}
    laptop:       {coco: [laptop], objects365: [Laptop], words: [laptop], app_agent: computer,
                   lvis: [laptop_computer]}
    food:         {coco: [banana, apple, sandwich, orange, broccoli, carrot, hot dog, pizza, donut, cake], ...}
    AirPods:      {coco: [], objects365: [earphone, Head Phone], words: [earphone, earbuds], app_agent: earbuds,
                   lvis: [earphone, headset]}
    phone:        {coco: [cell phone], objects365: [Cell Phone], words: [cell phone], app_agent: phone,
                   lvis: [cellular_telephone]}
    trash:        {coco: [], objects365: [Tissue, Napkin, Canned],
                   words: [crumpled paper, snack wrapper, empty can, used tissue],
                   lvis: [garbage, can, tissue_paper, napkin, paper_towel, receipt, bottle_cap, straw_(for_drinking)]}
  yoloe_extra_words: [person, keyboard, cup, tissue box, pen, cable]
  anchor_kinds: [laptop, mouse, book, phone, AirPods]   # a desk photo has at least one of these
  rare_kinds: [AirPods, trash, food]                    # photos with these are always kept
  max_pictures: 300
  seed: 7
  scenes: {covered_share: 0.3, busy_objects: 6, small_share: 0.01, small_kinds: [phone, mouse]}
  min_photos_per_cell: 5
  few_photos_warning: 15
  low_confidence: 0.25
  your_desk_gap: 0.15        # "does worse on your desk" when its score there is this much lower
  set_file: benchmarks/desk/desk-set.json
  photos: data/bench/desk/lvis
  your_photos: data/bench/desk-photos
```

Food's `objects365`, `words` and `lvis` lists:
- `words` is the snack agent's `labels` from `config/agents.yaml`, verbatim.
- `objects365` is the snack agent's labels that exist in Objects365 (check against `Objects365.yaml`).
- `lvis` is the LVIS food names matching the same foods.

**Interfaces:**
- Produces (all in `desk_set.py`):
  - `answers_for(lvis_there: set[str], lvis_not_exhaustive: set[str], coco_there: set[str], kinds: dict) -> dict[str, str]`: kind → `'there' | 'not there' | 'unknown'`, per the spec's rule.
  - `scenes_for(answers: dict[str, str], boxes: list[tuple[str | None, list[float]]], width: int, height: int, n_objects: int, rules: dict) -> list[str]`: boxes are (kind or None, [x, y, w, h] in pixels). Scene names: `covering`, `busy`, `small things`, `food`, `trash`, `tidy`. Covered share is the largest intersection with any one other box ÷ the box's own area.
  - `is_desk_picture(answers: dict[str, str], anchor_kinds: list[str]) -> bool`
  - `choose_pictures(candidates: list[dict], max_pictures: int, rare_kinds: list[str], seed: int) -> list[dict]`: candidates carry `answers`. Rare ones are always kept. The rest are `random.Random(seed).sample`d. Result sorted by `id`.
  - `build_desk_set(lvis: dict, coco_labels: dict[int, list[tuple[str, list[float]]]], desk_cfg: dict) -> list[dict]`: val2017 images only (`'val2017' in coco_url`). Each picture is `{'id': int, 'url': str, 'width': int, 'height': int, 'answers': {...}, 'scenes': [...]}`.
  - `read_your_desk(folder: Path, kinds: list[str]) -> list[dict]`: reads `folder / 'labels.yaml'` (`desk_01.jpg: [laptop, mouse]`). Unlisted kinds are `'not there'`, scenes are `['your desk']`, and `id` is the file name. Returns `[]` if the file is missing. An unknown kind raises `ValueError` naming it and the picture.
- `desk_data.py`:
  - `lvis_val(data_dir: Path) -> dict` downloads and unzips `https://dl.fbaipublicfiles.com/LVIS/lvis_v1_val.json.zip` once.
  - `coco_val_labels(data_dir: Path) -> dict` reads `labels/val2017/*.txt` from Ultralytics' `coco2017labels.zip` (asset download), using `coco.yaml` names and turning normalized boxes into pixels.
  - `download_photos(pictures: list[dict], folder: Path, log) -> int` fetches missing `<id>.jpg` from `url` and returns how many failed.

- [ ] **Step 1: Write `tests/test_desk_set.py`** with a tiny made-up `KINDS` (book: coco [book], lvis [book, paperback_book]; AirPods: coco [], lvis [earphone]). Cases:

```python
def test_coco_label_makes_it_there():
    assert answers_for(set(), set(), {'book'}, KINDS)['book'] == 'there'
def test_no_label_anywhere_is_not_there():
    assert answers_for(set(), set(), set(), KINDS)['book'] == 'not there'
def test_not_exhaustive_makes_it_unknown():
    assert answers_for(set(), {'paperback_book'}, set(), KINDS)['book'] == 'unknown'
def test_covering_scene_when_30_percent_of_a_kind_box_is_covered():
    boxes = [('book', [0, 0, 10, 10]), (None, [7, 0, 10, 10])]          # 30% covered
    assert 'covering' in scenes_for({'book': 'there'}, boxes, 100, 100, 2, RULES)
def test_tidy_when_no_other_scene():
    assert scenes_for({'book': 'there'}, [('book', [0, 0, 50, 50])], 100, 100, 1, RULES) == ['tidy']
def test_small_things_when_phone_box_is_under_1_percent(): ...
def test_desk_picture_needs_two_kinds_and_an_anchor(): ...
def test_choose_keeps_rare_and_is_repeatable():
    # 10 candidates, 2 with AirPods there, max 4: both AirPods kept, same result twice with seed 7
def test_build_desk_set_uses_only_val2017(): ...   # one val2017 and one train2017 image in a fake lvis dict
def test_your_desk_labels_unlisted_kinds_are_not_there(tmp_path): ...
def test_your_desk_labels_missing_file_gives_nothing(tmp_path): ...
def test_your_desk_labels_unknown_kind_is_an_error(tmp_path): ...
def test_words_match_the_apps_names():
    # reads the real config/benchmark.yaml and config/agents.yaml: every word of a kind with an
    # app_agent is in that agent's labels (so a yoloe pick works in the app unchanged)
```

- [ ] **Step 2: Run.** Expected: FAIL (no module).
- [ ] **Step 3: Implement `desk_set.py`, `desk_data.py`, and the desk config.** Then write `data/bench/desk-photos/labels.yaml` from the spec's table of Sam's six.
- [ ] **Step 4: Add `deskbench desk prepare` to `cli.py`.**
  1. It loads LVIS and the COCO labels.
  2. It checks every configured `lvis` name exists, else a `ValueError` lists the missing ones.
  3. It writes `desk.set_file` as JSON `{'made_from': 'LVIS v1 val + COCO val2017 labels', 'kinds': [...], 'pictures': [...]}`.
  4. It downloads the photos, then prints:
     - pictures per scene
     - "there" / "not there" counts per kind
     - failed downloads

  With `--keep-set`, it only downloads photos for the existing set file (for a second computer).
- [ ] **Step 5: Run the tests and ruff on the changed files.** Expected: pass, clean.
- [ ] **Step 6: Run it for real.** `cd benchmarks && uv run deskbench desk prepare`. Expected:
  - about 300 pictures, each scene non-empty
  - the counts printed, with `benchmarks/desk/desk-set.json` written and photos in `data/bench/desk/lvis/`

  Note the AirPods and trash counts for the report.

### Task 4: Scoring

**Files:**
- Create: `benchmarks/deskbench/desk_score.py`
- Test: `benchmarks/tests/test_desk_score.py`

**Interfaces:**
- Produces:
  - `app_thresholds(settings_path: Path, agents_path: Path, kinds: dict) -> dict[str, float]`: kind → the agent's `min_confidence` from `config/agents.yaml` (via `app_agent`), else `agents.min_confidence` from `config/settings.yaml`. Reads those two files only, never `settings.local.yaml`.
  - `name_map(kinds: dict, names: str) -> dict[str, str]`: lowercased model name → kind, for `names` in `coco | objects365 | words`.
  - `found_kinds(detections: list[tuple[str, float]], names: dict[str, str], threshold: dict[str, float] | float) -> set[str]`: names compared lowercased.
  - `score(pictures: list[dict], found: dict[str | int, set[str]], kinds: list[str], nameable: set[str]) -> dict`. Returns `{'kinds': {kind: {'there': int, 'not_there': int, 'found_rate': float | None, 'false_alarms': float | None, 'score': float | None}}, 'desk_score': float | None}`. The rules come from the spec:
    - score = found rate × (1 − false alarms)
    - no "there" pictures → `None`, and left out of the average
    - not nameable but has "there" pictures → 0
    - no "not there" pictures → found rate alone
    - unknown answers are skipped
  - `scene_scores(pictures, found, kinds, nameable, min_photos: int) -> dict[str, float | None]`: per scene, the average kind score over kinds with ≥ `min_photos` "there" pictures in that scene. `None` if there are none.

- [ ] **Step 1: Write `tests/test_desk_score.py`.**

```python
def test_kind_score_is_found_rate_times_one_minus_false_alarms():
    pics = [{'id': 1, 'answers': {'phone': 'there'}}, {'id': 2, 'answers': {'phone': 'there'}},
            {'id': 3, 'answers': {'phone': 'not there'}}, {'id': 4, 'answers': {'phone': 'not there'}}]
    s = score(pics, {1: {'phone'}, 2: set(), 3: {'phone'}, 4: set()}, ['phone'], {'phone'})
    assert s['kinds']['phone']['score'] == pytest.approx(0.5 * 0.5)
def test_unknown_is_never_scored(): ...            # a find on an 'unknown' picture changes nothing
def test_kind_the_model_cannot_name_scores_zero(): ...
def test_kind_with_no_there_pictures_is_left_out_of_the_average(): ...
def test_no_not_there_pictures_scores_found_rate_alone(): ...
def test_names_match_ignoring_case():
    assert found_kinds([('Cell Phone', 0.9)], name_map(KINDS, 'objects365'), 0.45) == {'phone'}
def test_threshold_per_kind():
    assert found_kinds([('bottle', 0.5)], {'bottle': 'water bottle'}, {'water bottle': 0.55}) == set()
def test_app_thresholds_use_agent_override(tmp_path):
    # settings: agents.min_confidence 0.45; agents.yaml: cup min_confidence 0.55
    assert app_thresholds(s, a, {'water bottle': {'app_agent': 'cup'}, 'trash': {}}) == \
        {'water bottle': 0.55, 'trash': 0.45}
def test_scene_cell_with_too_few_pictures_is_none(): ...
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and ruff.** Expected: pass, clean.

### Task 5: `deskbench desk run`

**Files:**
- Create: `benchmarks/deskbench/desk_one.py` (runs in its own process)
- Modify: `benchmarks/deskbench/results.py`, `benchmarks/deskbench/cli.py`
- Test: `benchmarks/tests/test_desk_run.py`, `benchmarks/tests/test_runner_results_report.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `results.append_row(path, row, columns: list[str] = COLUMNS)`, `results.all_rows(results_dir, skip: tuple[str, ...] = ('desk',))`
  - `results.desk_columns(kinds: list[str], scenes: list[str]) -> list[str]`. The order is:
    1. `run_id, device, commit, combo, model, kind, image_size, format, precision, status, error, started_at, pictures, desk_score, desk_score_low, your_desk_score`
    2. for each kind, with slugs (`water bottle` → `water_bottle`): `score_<slug>, found_<slug>, false_<slug>, there_<slug>`
    3. `scene_<slug>` for each scene, then `combo_time_s`
  - `desk_one` spec: `{model_path, task, image_size, floor: 0.25, pictures: [[id, path], ...]}`. Output: `{'detections': {id: [[name, conf], ...]}}`. One predict per picture at `conf=floor`.
  - `cli.desk_row(combo, kind_name, kind, detections, pictures, your_pictures, desk_cfg, thresholds) -> dict`: the scored row, pure. It computes `desk_score` at the app thresholds, `desk_score_low` at `desk.low_confidence`, and `your_desk_score` on Sam's six.
  - `deskbench desk run [--models ...] [--kinds ...] [--resume CSV]`:
    - Writes `benchmarks/results/desk/<UTC time>_<device>.csv` plus `.json` (environment + config, as `run` does).
    - Saves raw detections to `data/bench/desk/detections/<run_id>/<combo>.json`.
    - Uses `runner.run_all` with `cooldown=lambda: {}` and `results.desk_columns`. `run_all` needs a `columns` parameter passed through to `append_row`.

- [ ] **Step 1: Write the tests.**
  - `test_desk_columns_order_and_slugs`
  - `test_desk_row_scores_at_app_threshold_and_low` (fake detections where a phone at 0.3 counts only in `desk_score_low`)
  - `test_missing_picture_is_skipped_and_counted` (the picture list given to `desk_one` leaves out files that don't exist, and `pictures` in the row counts the ones used)
  - `test_unknown_column_is_refused` (still holds with `columns`)
  - `test_all_rows_skips_the_desk_folder`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Kinds a model can't name come from `name_map` being empty for that kind. `nameable` = kinds with at least one mapped name.
- [ ] **Step 4: Run all tests and ruff.** Expected: pass, clean.

### Task 6: Report: merged runs, desk test, three picks

**Files:**
- Modify: `benchmarks/deskbench/report.py`, `benchmarks/deskbench/cli.py` (`write_report`)
- Test: `benchmarks/tests/test_runner_results_report.py`

**Interfaces:**
- Produces:
  - `report.merged_rows(runs: dict[str, list[dict]]) -> list[dict]`: for one device, the last row per combination across runs, with runs sorted by `run_id`.
  - `report.pick_three(desk_rows: list[dict], device_rows: list[dict], camera_fps: float) -> list[dict]`:
    - Join on `combo`. Keep ok rows with `total_ms_p95 ≤ 1000/camera_fps` and a `desk_score`.
    - Take the best combination per `model` by `desk_score`; a tie goes to the higher `fps`.
    - Return the top 3 models, best first. Each is the desk row plus `fps` and `total_ms_p95`.
  - `report.render(rows, camera_fps, min_accuracy_share, desk_rows=(), desk_info=None) -> str`. `desk_info` is `{'kinds': [...], 'scenes': [...], 'counts': {kind: (there, not_there)}, 'your_desk': bool, 'gap': float, 'few': int}`.

The order in `render`:
1. The intro.
2. **"Three picks to try"**, from the Pi rows (merged) and the latest desk run. Each pick gets:
   - a line with model, size, format, desk score, Pi pictures/s and p95, and its score on Sam's six
   - "does worse on your desk" when `your_desk_score < desk_score − gap`
   - its best and worst kinds, in words
   - the try-it lines: `detector: {model: models/bench/<model>.pt or <model>-desk.pt, image_size: N}`, in `config/settings.local.yaml`, which Sam edits themself

   If no Pi rows qualify, say so.
3. **"Desk test"**:
   - one table row per model (its best combination): desk score, at 0.25, your desk, then each kind's score
   - a scenes table
   - counts per kind, with "only N photos" under `few`
   - one plain line each for: the trash name lists being a judgment call; the "not there" risk for AirPods/trash; and that the app has no trash agent, so trash a yoloe pick finds joins the chat as a new unknown object
   - "no photos of your own desk" when `your_desk` is false
4. The per-device sections. These now use `merged_rows`, and the header lists the runs used.
5. The history, unchanged.

- [ ] **Step 1: Write the tests.** Replace `test_report_uses_the_latest_run_per_device_and_lists_history` with `test_report_merges_runs_of_a_device`: an old run with combo `old` and a new run with `new` and a newer `old` row. Both show, the newer `old` values win, and the history lists both runs. Add:
  - `test_three_picks_are_fast_enough_and_different_models` (four models, one too slow, one with two combos)
  - `test_fewer_than_three_when_fewer_qualify`
  - `test_pick_flags_worse_on_your_desk`
  - `test_desk_section_says_when_there_are_few_photos`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** `cli.write_report` reads desk rows with `results.read_rows` over `results/desk/*.csv`, latest run only (by file name), and builds `desk_info` from config, `desk-set.json` and whether `labels.yaml` exists.
- [ ] **Step 4: Run all tests and ruff.** Expected: pass, clean.

### Task 7: Docs, then run it on the Mac

**Files:**
- Modify: `benchmarks/README.md`. Add a "Desk test" section in the existing style:
  - what it does
  - `uv sync --group words` (Mac only, for yoloe)
  - `uv run deskbench desk prepare`, then `uv run deskbench desk run`
  - how to add your own desk photos (the Pi live-view steps from this chat, saving to `data/bench/desk-photos/` and listing what's in each in `labels.yaml`)
  - the new desk CSV columns
  - the Pi steps for the new models: `prepare` and `rsync` on the Mac, rebuild the image on the Pi because `uv.lock` changed, then `docker compose run --rm -T bench run --kinds yolo26 yolo26-objv1 yoloe-26` in the background as before
- Modify: `config/benchmark.yaml` header comment, to mention the desk test.

- [ ] **Step 1: Write the docs.**
- [ ] **Step 2: Run the full test suite and ruff on every changed file.** Show the output.
- [ ] **Step 3: Make all models on the Mac.** `cd benchmarks && uv run deskbench prepare`. Expected: `(0 failed)`. If a yoloe ncnn export fails, set that kind's formats to `{onnx: [fp32]}`, as the spec allows, and note it for the report.
- [ ] **Step 4: Run the desk test.** `uv run deskbench desk run` (in the background; about 99 combinations). Expected: every row ok or a clear failure, and `benchmarks/results/desk/<run>.csv` written.
- [ ] **Step 5: Rebuild and check the report.** `uv run deskbench report`. Read `benchmarks/results/README.md`. The desk section and counts are there. "Three picks" can only use yolo11 until the Pi run is in, and should say so.
- [ ] **Step 6: Hand over to Sam.** Give the exact commands for copying the models to the Pi and running the Pi speed run, labeled **on your Mac** / **on the Pi**. After the run, the Mac fetches the results and reruns `deskbench report`, and the three picks are final.
