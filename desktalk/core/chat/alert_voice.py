"""
Alerts in an agent's voice: "the cup is getting close to me!"

An alert that carries its own text (a pet, clutter, a lost item ...) is sent as written. A proximity
alert is put into words by the AI, grounded in the detector's own words for both objects; when the AI
can't help (or its line is cut off, dramatic or for another agent), its fixed line is used: the agent's own
too_close, or the desk edge's (config/prompts.yaml → alert). A follow-up of the same alert replies to what was
said before (a warning about a different desk edge is a new one). When nothing in the chat can say it, Lens does,
without the AI: "Heads up: scissors next to the cell phone". With chat.ai_writes_warnings off (the Pi) the AI is never
asked, so a warning can't wait for one. A speaker that isn't in the chat (a Demo tab warning) sends its card along,
so the page can draw it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from desktalk.config import ChatSettings
from desktalk.config.prompts import AlertPrompts
from desktalk.core.chat import prompts
from desktalk.core.chat.agent_ai import AgentAI
from desktalk.core.chat.replies import looks_cut_off, response_violates_evidence
from desktalk.domain import LENS, AgentProfile, Alert, SeenObject
from desktalk.ports import DeskScene, Profiles, RosterView


class AlertVoice:
    """`roster`: who is in the chat (anyone else gets a card with their warning). `live_context()`: the desk right
    now for the AI (to check the AI's line against). `drink_labels`: the detector words that count as drinks when no
    agent speaks for hydration (settings → safety.liquids)."""

    def __init__(self, ai: AgentAI, profiles: Profiles, roster: RosterView, scene: DeskScene,
                 live_context: Callable[[], Mapping[str, Any]], words: AlertPrompts, chat: ChatSettings,
                 drink_labels: Iterable[str]):
        self._ai = ai
        self._profiles = profiles
        self._roster = roster
        self._scene = scene
        self._live_context = live_context
        self._words = words
        self._chat = chat
        self._drink_labels = frozenset(drink_labels)
        self._said_about: dict[str, dict[str, str]] = {}    # alert key → last thing said about it (follow-ups)

    def message_for_alert(self, ev: Alert) -> dict[str, Any] | None:
        """The chat message for an alert, or None when there is nothing to say. A speaker that isn't in the chat
        sends its card along, so the page can draw someone it hasn't met."""
        msg = self._message(ev.to_dict())
        if msg is None or msg['character'] == LENS or msg['character'] in self._roster.member_ids():
            return msg
        profile = self._profiles.get(msg['character'])
        return {**msg, 'card': profile.public()} if profile else msg

    def _message(self, d: Mapping[str, Any]) -> dict[str, Any] | None:
        pid = d.get('agent') or LENS
        snapshot = d.get('snapshot')
        if d.get('kind') != 'proximity' or d.get('text'):
            # Alerts that carry their own text (pet, clutter, lost item ...) are sent as written.
            if not d.get('text'):
                return None
            speaker = pid if self._profiles.get(pid) else LENS
            msg = {'type': 'group_message', 'character': speaker, 'text': d['text'], 'alert': d.get('feature', '')}
            if snapshot:
                msg['snapshot'] = snapshot
            return msg
        if pid == LENS:
            return self._lens_alert(d)
        a = self._profiles.get(pid)
        if not a:
            return None
        follow_up = int(d.get('follow_up') or 0)
        key = f'proximity_{pid}_{d.get("near") or ""}' + (f'_{d["edge"]}' if d.get('edge') else '')
        prev = self._said_about.get(key) if follow_up > 0 else None
        text = self._ai_alert(a, d, prev, follow_up) if self._chat.ai_writes_warnings else None
        text = text or self._fixed_line(a, d, follow_up)
        msg = {'type': 'group_message', 'character': pid, 'text': text, 'alert': 'proximity'}
        if snapshot:
            msg['snapshot'] = snapshot
        if prev:
            msg['reply_to'] = {'character': prev['character'], 'text': prev['text']}
        self._said_about[key] = {'character': pid, 'text': text}
        return msg

    def _fixed_line(self, a: AgentProfile, d: Mapping[str, Any], follow_up: int) -> str:
        """The warning's own words, with no AI. `follow_up`: how many times it was said already."""
        w = self._words
        if d.get('edge'):
            return w.edge_again if follow_up else w.edge
        other = self._profiles.get(d.get('near') or '')
        if other:
            return w.still_too_close.format(other=other.name) if follow_up else a.too_close_text(other.name)
        if d.get('rule_label'):
            return w.heads_up.format(rule=d['rule_label'].rstrip('.'))
        return w.too_close

    def _lens_alert(self, d: Mapping[str, Any]) -> dict[str, Any]:
        """Lens's warning (it has no personality, so no AI): what is in which thing's zone, or at the desk edge."""
        w = self._words
        if d.get('intruder'):
            text = w.lens_zone.format(intruder=self._label(d['intruder']), target=self._label(d.get('near') or ''),
                                      zone=d.get('rule_label') or '')
        elif d.get('object'):
            text = w.lens_edge.format(object=self._label(d['object']))
        else:
            text = w.too_close
        msg = {'type': 'group_message', 'character': LENS, 'text': text, 'alert': 'proximity'}
        if d.get('snapshot'):
            msg['snapshot'] = d['snapshot']
        return msg

    def _label(self, agent_id: str) -> str:
        """The detector's word for a thing on the desk (in the chat or not)."""
        seen = self._objects().get(agent_id)
        return seen.label if seen else self._words.unknown_other

    def _objects(self) -> dict[str, SeenObject]:
        """Everything on the desk: the warnings' own list, and the chat's (which can see weaker boxes)."""
        return {**self._scene.members(), **self._scene.on_desk()}

    def _describe(self, a: AgentProfile, d: Mapping[str, Any]) -> str:
        """The situation, in the detector's own words, so the AI doesn't invent spills or damage."""
        w = self._words
        other_id = d.get('near') or ''
        objects = self._objects()
        self_label = objects[a.id].label if a.id in objects else w.unknown_label
        other = self._profiles.get(other_id)
        other_label = objects[other_id].label if other_id in objects else w.unknown_label
        other_name = other.name if other else w.unknown_other
        drinks = self._profiles.labels_for_habit('hydration') or self._drink_labels
        if other_label in drinks:
            drink_note = w.other_is_drink.format(other_label=other_label)
        elif self_label in drinks:
            drink_note = w.self_is_drink.format(self_label=self_label)
        else:
            drink_note = w.no_drink
        risk = d.get('rule_label') or self._profiles.relationship_risk(a.id, other_id) or \
            w.same_area.format(self_label=self_label, other_label=other_label)
        return w.grounding.format(self_label=self_label, other_label=other_label, name=a.name, other_name=other_name,
                                  drink_note=drink_note, risk=risk)

    def _ai_alert(self, a: AgentProfile, d: Mapping[str, Any], prev: Mapping[str, str] | None,
                  follow_up: int) -> str | None:
        situation = self._describe(a, d)
        previous = prev['text'] if prev else None
        text = self._ai.ask([{'role': 'system', 'content': prompts.event_system(self._words, a)},
                             {'role': 'user', 'content': prompts.event_user(self._words, a, situation, previous,
                                                                            follow_up)}],
                            'alert', a, background=True)
        if not text or looks_cut_off(text, self._chat.min_reply_words):
            return None
        text = self._ai.one_speaker(text, a)
        if not text or response_violates_evidence(text, self._live_context(), '', []):
            return None
        return text
