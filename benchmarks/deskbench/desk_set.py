"""
The desk pictures and their right answers.

Public photos: COCO val2017 photos (the COCO models never trained on them) with two
label sets. COCO's labels are complete for its 80 kinds; LVIS knows far more kinds
(earphones, some trash) but doesn't check every kind in every photo. So for each
photo and each of Sam's kinds the right answer is "there", "not there" or "unknown",
and unknown is never scored. Scenes (covering, busy, ...) are worked out from the
LVIS boxes. See benchmarks/desk-test-design.md.

Sam's own photos: data/bench/desk-photos/labels.yaml lists what's in each one;
anything not listed is not there.
"""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

import yaml

SCENES = ['covering', 'busy', 'small things', 'food', 'trash', 'tidy']


def answers_for(lvis_there: set[str], lvis_not_exhaustive: set[str], coco_there: set[str],
                kinds: dict) -> dict[str, str]:
    answers = {}
    for name, kind in kinds.items():
        if lvis_there & set(kind['lvis']) or coco_there & set(kind['coco']):
            answers[name] = 'there'
        elif lvis_not_exhaustive & set(kind['lvis']):
            answers[name] = 'unknown'
        else:
            answers[name] = 'not there'
    return answers


def _covered_share(box: list[float], other: list[float]) -> float:
    x, y, w, h = box
    ox, oy, ow, oh = other
    iw = max(0.0, min(x + w, ox + ow) - max(x, ox))
    ih = max(0.0, min(y + h, oy + oh) - max(y, oy))
    return iw * ih / (w * h) if w * h else 0.0


def scenes_for(answers: dict[str, str], boxes: list[tuple[str | None, list[float]]], width: int, height: int,
               n_objects: int, rules: dict) -> list[str]:
    """boxes: (one of Sam's kinds or None, [x, y, w, h] in pixels)."""
    there = {k for k, a in answers.items() if a == 'there'}
    scenes = []
    if any(kind in there and any(_covered_share(box, other) >= rules['covered_share']
                                 for j, (_, other) in enumerate(boxes) if j != i)
           for i, (kind, box) in enumerate(boxes)):
        scenes.append('covering')
    if n_objects >= rules['busy_objects']:
        scenes.append('busy')
    small = rules['small_share'] * width * height
    if 'AirPods' in there or any(kind in rules['small_kinds'] and box[2] * box[3] < small for kind, box in boxes):
        scenes.append('small things')
    scenes += [k for k in ('food', 'trash') if k in there]
    return scenes or ['tidy']


def is_desk_picture(answers: dict[str, str], anchor_kinds: list[str]) -> bool:
    there = [k for k, a in answers.items() if a == 'there']
    return len(there) >= 2 and any(k in anchor_kinds for k in there)


def choose_pictures(candidates: list[dict], max_pictures: int, rare_kinds: list[str], seed: int) -> list[dict]:
    """All pictures with a rare kind in them, then a repeatable random pick of the rest, up to max_pictures."""
    candidates = sorted(candidates, key=lambda p: p['id'])
    rare = [p for p in candidates if any(p['answers'].get(k) == 'there' for k in rare_kinds)]
    rest = [p for p in candidates if p not in rare]
    room = max(0, max_pictures - len(rare))
    picked = rare + random.Random(seed).sample(rest, min(room, len(rest)))
    return sorted(picked, key=lambda p: p['id'])


def build_desk_set(lvis: dict, coco_labels: dict[int, list[tuple[str, list[float]]]], desk_cfg: dict) -> list[dict]:
    kinds = desk_cfg['kinds']
    category = {c['id']: c['name'] for c in lvis['categories']}
    kind_of_lvis = {name: kind for kind, k in kinds.items() for name in k['lvis']}
    annotations = defaultdict(list)
    for a in lvis['annotations']:
        annotations[a['image_id']].append(a)

    candidates = []
    for image in lvis['images']:
        if 'val2017' not in image['coco_url']:
            continue                    # LVIS val also has COCO train2017 photos, which the COCO models trained on
        anns = annotations[image['id']]
        lvis_there = {category[a['category_id']] for a in anns}
        not_exhaustive = {category[c] for c in image.get('not_exhaustive_category_ids', [])}
        coco_there = {name for name, _ in coco_labels.get(image['id'], [])}
        answers = answers_for(lvis_there, not_exhaustive, coco_there, kinds)
        if not is_desk_picture(answers, desk_cfg['anchor_kinds']):
            continue
        # Things lie on desks, tables and plates; that isn't covering, so surfaces are left out of the scenes
        things = [a for a in anns if category[a['category_id']] not in desk_cfg['scenes'].get('surfaces', [])]
        boxes = [(kind_of_lvis.get(category[a['category_id']]), a['bbox']) for a in things]
        candidates.append({'id': image['id'], 'url': image['coco_url'], 'width': image['width'],
                           'height': image['height'], 'answers': answers,
                           'scenes': scenes_for(answers, boxes, image['width'], image['height'], len(things),
                                                desk_cfg['scenes'])})
    return choose_pictures(candidates, desk_cfg['max_pictures'], desk_cfg['rare_kinds'], desk_cfg['seed'])


def read_your_desk(folder: Path, kinds: list[str]) -> list[dict]:
    labels_file = folder / 'labels.yaml'
    if not labels_file.exists():
        return []
    pictures = []
    for name, there in (yaml.safe_load(labels_file.read_text()) or {}).items():
        unknown = set(there or []) - set(kinds)
        if unknown:
            raise ValueError(f'{labels_file}: {name} lists {", ".join(sorted(unknown))}, which is not one of '
                             f'the desk kinds ({", ".join(kinds)})')
        pictures.append({'id': name, 'path': folder / name, 'scenes': ['your desk'],
                         'answers': {k: 'there' if k in (there or []) else 'not there' for k in kinds}})
    return pictures
