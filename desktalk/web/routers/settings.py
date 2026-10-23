"""
The Settings page:

    GET /api/rules       the danger rules and the desk corners (config/safety_rules.json, or the built-in ones)
    PUT /api/rules       change some of them; marking the desk corners also notes how the phone was tilted
    GET /api/scenarios   which checks are on, and the lost items seen lately
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from desktalk.web.deps import Desk
from desktalk.web.schemas import Ok, RuleChanges

# The things a danger zone can be set for on the Settings page.
ZONE_OBJECTS = [
    'cell phone', 'laptop', 'keyboard', 'mouse', 'remote', 'book', 'notepaper', 'tablet',
    'bottle', 'cup', 'wine glass', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'pizza', 'donut', 'cake',
    'scissors', 'clock', 'vase', 'tv', 'earphone', 'key', 'watch', 'pen/pencil',
]
RULE_PARTS = ('danger_zones', 'edge_proximity', 'chain_reaction', 'temporal')
TILT_DECIMALS = 2

router = APIRouter()


@router.get('/api/rules')
def rules(desk: Desk) -> dict[str, Any]:
    return {**desk.safety_rules.for_settings_page(), 'available_objects': ZONE_OBJECTS}


@router.put('/api/rules')
def change_rules(desk: Desk, sent: RuleChanges | None = None) -> Ok:
    if sent is None or not sent.sent():
        raise HTTPException(400, 'Empty payload')
    changes = {part: getattr(sent, part) for part in RULE_PARTS if part in sent.sent()}
    if 'desk_outline' in sent.sent():
        try:
            corners = desk.safety_rules.clean_outline(sent.desk_outline)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        changes['desk_outline'] = corners
        # How the phone was tilted when the desk was marked, so Lens can tell if it gets bumped.
        tilt = desk.phone_source.recent_gravity() if desk.phone_source is not None else None
        changes['camera_reference'] = {'gravity': [round(v, TILT_DECIMALS) for v in tilt]} if corners and tilt else None
        desk.camera_health.forget('bumped')
    desk.safety_rules.save(changes)
    return Ok()


@router.get('/api/scenarios')
def scenarios(desk: Desk) -> dict[str, Any]:
    now = desk.clock.now()
    lost = {item: {'last_seen_ago_sec': int(now - seen['last_seen']), 'alerted': seen['alerted']}
            for item, seen in desk.lost_items.sightings().items() if seen.get('last_seen')}
    with desk.desk.lock:
        switched_on = dict(desk.desk.active_scenarios)
    return {'scenarios': switched_on, 'lost_objects': lost,
            'lost_items': {item: {'name': about['name']} for item, about in desk.lost_items.items.items()}}
