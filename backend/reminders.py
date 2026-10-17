"""
Evidence-based habit reminders.

Each character only speaks up when the camera has seen a real reason, and the
message says what that reason is. These are plain functions with no camera or
AI code, so they're easy to test.

    evidence_for('glug', state, now)  ->  ("No sips in 47 minutes.", "I haven't seen you ...")
                                       or  None (nothing to say)
"""

BREAK_AFTER_MIN = 50        # Computer: time at the desk without a break
SIP_AFTER_MIN = 45          # Cup: time without a sip
SNACK_AFTER_MIN = 20        # Snack: food sitting on the desk
TODO_AFTER_MIN = 40         # Paper: time between to-do check-ins
REPEAT_AFTER_MIN = 30       # the same character waits at least this long before repeating


def _minutes(now, since):
    return int((now - since) // 60) if since else 0


def evidence_for(cid, state, now):
    """Return (evidence, fallback_message) for character `cid`, or None.

    `state` keys (all optional):
      session_start, last_break_ts, last_sip_ts, cup_on_desk,
      snack_since, snack_name, open_todos (list of str), last_fire (dict cid -> ts)
    `evidence` is a fact for the AI to mention; `fallback_message` is used without AI.
    """
    start = state.get('session_start')
    if not start:
        return None
    last_fire = (state.get('last_fire') or {}).get(cid, 0) or 0

    if cid == 'monty':
        since = max(start, state.get('last_break_ts') or 0, last_fire)
        m = _minutes(now, max(start, state.get('last_break_ts') or 0))
        if now - since >= BREAK_AFTER_MIN * 60:
            return (
                f'The user has been at the desk for {m} minutes without a break.',
                f"You've been working for {m} minutes straight. Could you look at something far away "
                f"for 20 seconds, or stand up and stretch? My fans would appreciate it too.",
            )
        return None

    if cid == 'glug':
        since = max(start, state.get('last_sip_ts') or 0)
        m = _minutes(now, since)
        if now - max(since, last_fire) >= SIP_AFTER_MIN * 60:
            if state.get('cup_on_desk'):
                return (
                    f'The cup has not been picked up in {m} minutes.',
                    f"Nobody has picked me up in {m} minutes. I'm still full! Have a sip?",
                )
            return (
                f'No cup or drink has been seen on the desk for {m} minutes.',
                f"I haven't seen you drink anything in {m} minutes. Want to grab some water?",
            )
        return None

    if cid == 'munch':
        since = state.get('snack_since')
        if since and now - max(since, last_fire) >= SNACK_AFTER_MIN * 60:
            name = state.get('snack_name') or 'snack'
            m = _minutes(now, since)
            return (
                f'A {name} has been on the desk for {m} minutes.',
                f"This {name} has been next to you for {m} minutes. Eat it or move it away from the keyboard?",
            )
        return None

    if cid == 'sheets':
        todos = [t for t in (state.get('open_todos') or []) if t]
        if todos and now - max(start, last_fire) >= TODO_AFTER_MIN * 60:
            n = len(todos)
            first = todos[0][:60]
            plural = 's' if n != 1 else ''
            return (
                f'The user has {n} open task{plural} on their list, starting with "{first}".',
                f'You still have {n} open task{plural}, like "{first}". Want to pick one for the next 30 minutes?',
            )
        return None

    return None


def pick_reminder(state, now, order=('monty', 'glug', 'munch', 'sheets')):
    """The character who should speak now, with its evidence, or None.
    Characters who spoke least recently go first."""
    last_fire = state.get('last_fire') or {}
    for cid in sorted(order, key=lambda c: last_fire.get(c, 0) or 0):
        if now - (last_fire.get(cid, 0) or 0) < REPEAT_AFTER_MIN * 60:
            continue
        ev = evidence_for(cid, state, now)
        if ev:
            return cid, ev[0], ev[1]
    return None
