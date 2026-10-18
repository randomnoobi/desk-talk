"""
The web API the computer app (app/) and the Raspberry Pi talk to.

    Camera     /api/pi/frame  /api/pi/config  /api/phone/info  /api/video_feed…  /api/latest_frame…
    Live       /api/live  /api/status  /api/detections  /api/safety  /api/events (messages, SSE)
    Agents     /api/agents  /api/chat  /api/context  /api/chat_todos…  /api/widget_event
    Habits     /api/habits/today  /api/habits/log  /api/habit_log  /api/daily_stats  /api/preferences
    Event log  /api/log/days  /api/log/events  /api/log/presence  /api/log/detections  /api/log/status  /api/log/compact
    Settings   /api/rules  /api/scenarios
    Photos     /api/snapshot/<id>  /api/snapshots
"""

from __future__ import annotations

import json
import queue
import time
import uuid

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

from .. import paths
from ..chat.context import build_live_context, group_speaker_order, sanitize_chat_history
from ..chat.habit_vlm import HabitSceneVlmThread
from ..chat.widgets import apply_habit_actions, normalize_todo
from ..util import local_day
from ..vision import relationships
from .mjpeg import NO_CACHE, mjpeg_response

AVAILABLE_ZONE_OBJECTS = [
    'cell phone', 'laptop', 'keyboard', 'mouse', 'remote', 'book', 'notepaper', 'tablet',
    'bottle', 'cup', 'wine glass', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'pizza', 'donut', 'cake',
    'scissors', 'clock', 'vase', 'tv', 'earphone', 'key', 'watch', 'pen/pencil',
]


def create_web_app(app) -> Flask:
    web = Flask(__name__, static_folder=str(paths.APP_DIR), static_url_path='')
    web.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
    CORS(web)
    desk, gen = app.desk, app.generator
    vision_habits = getattr(app, 'vision_habits', None) or HabitSceneVlmThread(app)
    app.phone_url = getattr(app, 'phone_url', '')
    app.phone_qr_svg = getattr(app, 'phone_qr_svg', None)

    def is_local():
        return request.remote_addr in ('127.0.0.1', '::1')

    def cam_index():
        with desk.lock:
            cams = sorted(desk.cameras)
        return cams[0] if cams else 0

    @web.route('/')
    def index():
        return send_from_directory(paths.APP_DIR, 'index.html')

    # ════════════════════════════════════════════════════════════════
    #  Raspberry Pi camera
    # ════════════════════════════════════════════════════════════════
    def pi_key_ok():
        return request.headers.get('X-Desk-Key') == app.pair_key

    @web.route('/api/pi/config')
    def pi_config():
        """The Pi asks for its settings (so they only live in config/settings.yaml)."""
        if not pi_key_ok():
            return jsonify({'error': 'Pairing key does not match. Copy the command printed by server.py.'}), 403
        return jsonify({'pi': dict(app.settings.get('pi') or {}), 'camera_source': app.source_kind})

    @web.route('/api/pi/frame', methods=['POST'])
    def pi_frame():
        if not pi_key_ok():
            return jsonify({'error': 'Pairing key does not match. Copy the command printed by server.py.'}), 403
        if getattr(app.source, 'kind', '') != 'pi':
            return jsonify({'error': f'This computer is set to camera.source: {app.source_kind}. '
                                     f'Set it to pi in config/settings.yaml.'}), 409
        h = request.headers
        meta = {'frame_id': h.get('X-Frame-Id', ''), 'camera': h.get('X-Pi-Camera', '')}
        for key, header, cast in (('taken_at', 'X-Taken-At', float), ('lux', 'X-Lux', float),
                                  ('photos_stored', 'X-Pi-Photos', int), ('disk_free_mb', 'X-Pi-Free-MB', int),
                                  ('last_cleanup_ts', 'X-Pi-Last-Cleanup', float)):
            raw = h.get(header)
            if raw not in (None, ''):
                try:
                    meta[key] = cast(raw)
                except ValueError:
                    pass
        if not app.source.push_jpeg(request.get_data(), meta):
            return jsonify({'error': 'not a JPEG image'}), 400
        return jsonify({'ok': True, 'fps': app.source.fps, 'tip': app.camera_health.phone_tip()})

    @web.route('/api/phone/info')
    def phone_info():
        local = is_local()
        return jsonify({'phone_mode': app.phone_is_camera, 'connected': app.phone_is_camera and app.camera_connected(),
                        'fps': getattr(app.source, 'fps', 0), 'url': app.phone_url if local else '',
                        'qr_svg': app.phone_qr_svg if local else None})

    # ════════════════════════════════════════════════════════════════
    #  Video and photos
    # ════════════════════════════════════════════════════════════════
    def annotated(ci):
        def get():
            with desk.lock:
                return desk.frames.get(ci)
        return mjpeg_response(get)

    def plain(ci):
        def get():
            with desk.lock:
                return desk.raw_frames.get(ci) or desk.frames.get(ci)
        return mjpeg_response(get)

    web.add_url_rule('/api/video_feed', 'video_feed', lambda: annotated(cam_index()))
    web.add_url_rule('/api/video_feed/<int:ci>', 'video_feed_cam', annotated)
    web.add_url_rule('/api/video_feed_raw', 'video_feed_raw', lambda: plain(cam_index()))
    web.add_url_rule('/api/video_feed_raw/<int:ci>', 'video_feed_raw_cam', plain)

    @web.route('/api/latest_frame/<int:ci>')
    def latest_frame(ci):
        with desk.lock:
            j = desk.frames.get(ci) or desk.raw_frames.get(ci)
        return Response(bytes(j), mimetype='image/jpeg', headers=dict(NO_CACHE)) if j else Response(status=404)

    @web.route('/api/latest_frame_raw/<int:ci>')
    def latest_frame_raw(ci):
        with desk.lock:
            j = desk.raw_frames.get(ci) or desk.frames.get(ci)
        return Response(bytes(j), mimetype='image/jpeg', headers=dict(NO_CACHE)) if j else Response(status=404)

    @web.route('/api/snapshot/<snap_id>')
    def get_snapshot(snap_id):
        with desk.lock:
            jpeg = desk.snapshots.get(snap_id)
        if not jpeg:
            return '', 404
        return Response(jpeg, mimetype='image/jpeg', headers={'Cache-Control': 'public, max-age=3600'})

    @web.route('/api/snapshots')
    def list_snapshots():
        with desk.lock:
            items = [dict(x) for x in desk.snapshot_order]
        return jsonify({'items': items})

    # ════════════════════════════════════════════════════════════════
    #  Live status
    # ════════════════════════════════════════════════════════════════
    def agent_of(label):
        p = app.profiles.for_label(label)
        return p.id if p else None

    def detections_payload():
        with desk.lock:
            fw, fh = desk.frame_w, desk.frame_h
            objs = dict(desk.objects)
            seen = dict(desk.seen_labels)
            rel = (desk.rel_result or {}).get('relationships') or []
            vlm_habit, vlm_ts = dict(desk.vlm_habit_state or {}), desk.vlm_habit_ts
            lost_vlm = sorted(desk.lost_last_vlm_visible)
            light = dict(desk.light)
        out_objs = {}
        for pid, det in objs.items():
            p = app.profiles.get(pid)
            if p:
                out_objs[pid] = {'name': p.name, 'icon': p.icon, 'class': det['class_name'],
                                 'confidence': round(det['conf'], 2), 'center': [round(c) for c in det['center']],
                                 'bbox': [round(float(x)) for x in det['bbox']], 'cam': det.get('cam', 0)}
        agent_labels = {d['class_name'] for d in objs.values()}
        members = set(app.roster.member_ids())
        agent_labels |= {l for l in seen if agent_of(l) in members}     # e.g. a mouse is part of Computer
        lost_labels = set()
        for it in app.lost_items.items.values():
            lost_labels |= it['labels']
        extras = sorted(({'yolo_class': l, 'confidence': round(o['conf'], 2),
                          'bbox': [round(float(x)) for x in o['bbox']], 'highlight': l in lost_labels,
                          'ignored': app.profiles.is_ignored(l)}
                         for l, o in seen.items() if l not in agent_labels), key=lambda x: -x['confidence'])
        edges = [{'from': r.get('intruder'), 'to': r.get('target'), 'kind': 'danger_zone', 'label': r.get('zone_label', '')}
                 for r in rel if r.get('type') == 'danger_zone']
        return {'objects': out_objs, 'extras': extras, 'timestamp': time.time(), 'frame': {'w': fw, 'h': fh},
                'relationship_edges': edges, 'vlm_habit': vlm_habit, 'vlm_habit_ts': vlm_ts,
                'lost_vlm_labels': lost_vlm, 'light': light}

    def safety_payload():
        with desk.lock:
            state, dangers, rr = desk.safety_state, list(desk.safety_dangers), desk.rel_result
        rr = rr or {}
        return {'state': state, 'dangers': dangers, 'rules_loaded': app.rules_loaded,
                'risk_level': int(rr.get('risk_level', 0)), 'explanations': list(rr.get('explanations') or []),
                'risk_events': list(rr.get('risk_events') or []), 'relationships': list(rr.get('relationships') or [])}

    def camera_payload():
        src = app.source
        out = {'source': app.source_kind, 'connected': app.camera_connected(), 'fps': getattr(src, 'fps', 0),
               'frames_received': getattr(src, 'frames_received', None),
               'yolo_ms': round(app.producer.last_ms) if app.producer else None,
               'model': app.detector.label if app.detector is not None and hasattr(app.detector, 'label') else None}
        if app.source_kind == 'pi':
            out['pi'] = {'last_frame_id': src.last_frame_id, **src.pi_info}
        return out

    @web.route('/api/detections')
    def detections():
        return jsonify(detections_payload())

    @web.route('/api/safety')
    def safety_status():
        return jsonify(safety_payload())

    @web.route('/api/safety', methods=['POST'])
    def safety_receive():
        """Safety updates from the standalone pipeline (main.py)."""
        data = request.json or {}
        with desk.lock:
            if data.get('type') == 'danger_alert':
                desk.safety_state, desk.safety_dangers = 'DANGEROUS', data.get('pairs', [])
            elif data.get('type') == 'safe_status':
                desk.safety_state, desk.safety_dangers = 'SAFE', []
        return jsonify({'ok': True})

    @web.route('/api/live')
    def live():
        """Everything the desk cam panel needs in one request (the app asks every 1.5 s)."""
        out = {'phone_mode': app.phone_is_camera, 'camera_source': app.source_kind,
               'camera': app.camera_connected(), 'camera_index': cam_index(),
               'camera_info': camera_payload(), 'detections': detections_payload(), 'safety': safety_payload(),
               'camera_issues': app.camera_health.issues(), 'agents': app.roster.snapshot(),
               'ai': {'ready': app.llm.ready, 'label': app.llm.label, 'problem': app.llm.problem()}}
        if app.phone_is_camera:
            local = is_local()
            out['phone'] = {'fps': app.source.fps, 'url': app.phone_url if local else '',
                            'qr_svg': app.phone_qr_svg if local and request.args.get('qr') else None}
        if app.source_kind == 'pi' and is_local():
            out['pi_command'] = pi_command()
        return jsonify(out)

    def pi_command():
        from ..vision.pairing import lan_ip
        return (f'python3 pi/camera_node.py --server http://{lan_ip()}:{int(app.settings.web.get("port", 5000))} '
                f'--key {app.pair_key}')

    @web.route('/api/status')
    def status():
        with desk.lock:
            safety = desk.safety_state
            rr = desk.rel_result or {}
        return jsonify({'status': 'running', 'camera': app.camera_connected(), 'camera_source': app.source_kind,
                        'phone_mode': app.phone_is_camera, 'camera_indices': [cam_index()] if app.camera_connected() else [],
                        'llm': app.llm.ready, 'llm_label': app.llm.label, 'agents': app.roster.member_ids(),
                        'safety_state': safety, 'safety_rules': app.rules_loaded,
                        'risk_level': rr.get('risk_level', 0), 'uptime_sec': round(time.time() - app.started_at)})

    # ════════════════════════════════════════════════════════════════
    #  Agents and chat
    # ════════════════════════════════════════════════════════════════
    @web.route('/api/agents')
    def agents():
        return jsonify({'agents': app.roster.snapshot(),
                        'lens': {'id': 'lens', 'name': 'Lens', 'art': 'art/lens.svg', 'color': 'coral',
                                 'about': 'The camera. Speaks up when the view needs fixing.'}})

    @web.route('/api/events')
    def events_stream():
        q = gen.subscribe()

        def stream():
            try:
                yield 'retry: 2000\n\n'
                while True:
                    try:
                        msg = q.get(timeout=25)
                        yield f'data: {json.dumps(msg, default=str)}\n\n'
                    except queue.Empty:
                        yield ': keepalive\n\n'
            finally:
                gen.unsubscribe(q)
        return Response(stream(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @web.route('/api/messages/recent')
    def recent_messages():
        """The last chat messages and join / leave notes, for a page that just opened."""
        with gen._lock:
            return jsonify({'messages': list(gen.recent)})

    @web.route('/api/activity', methods=['POST'])
    def user_activity():
        """The app pings this while you type so idle chatter holds off."""
        desk.last_user_activity = time.time()
        return ('', 204)

    @web.route('/api/chat', methods=['POST'])
    def chat():
        data = request.json or {}
        desk.last_user_activity = time.time()
        chat_id = data.get('chat', 'group')
        message = (data.get('message') or '').strip()
        if not message:
            return jsonify({'error': 'empty message'}), 400
        if app.lost_items.is_lost_query(message):
            rep = app.lost_items.answer(message)
            speaker = chat_id if (chat_id != 'group' and app.profiles.get(chat_id)) else rep['speaker']
            return jsonify({'messages': [{'character': speaker, 'text': rep['text'], 'cta': rep['cta'],
                                          'id': uuid.uuid4().hex[:12]}], 'lost_object_lookup': True})
        history = sanitize_chat_history(data.get('recent_messages') or data.get('history'))
        if chat_id == 'group':
            words = app.profiles.mention_words()
            members = app.roster.member_ids()
            known = list(app.profiles.profiles)
            speakers = group_speaker_order(message, words, members, known)
            if not speakers:
                return jsonify({'messages': [], 'error': 'Nobody is on the desk yet. Put something in front of the '
                                                         'camera, or @mention someone who was here earlier.'})
            rolling, prev, sent = list(history), None, []
            for pid in speakers:
                text, actions = gen.generate_chat_response(pid, message, rolling)
                if not text:
                    continue
                apply_habit_actions(app.habits, actions)
                msg = {'type': 'group_message', 'character': pid, 'text': text, 'id': uuid.uuid4().hex[:12]}
                if prev:
                    msg['reply_to'] = {'character': prev['character'], 'text': prev['text']}
                gen.broadcast(msg)     # shows up right away; also in the reply (same id) in case SSE dropped
                rolling.append({'role': 'assistant', 'name': app.profiles.get(pid).name, 'text': text})
                prev = msg
                sent.append(msg)
            out = {'messages': sent, 'streamed': True}
            if not sent:
                out['error'] = gen.no_reply_reason()
            return jsonify(out)
        if not app.profiles.get(chat_id):
            return jsonify({'error': 'unknown chat'}), 400
        if 'widget' in data:
            w = normalize_todo(data.get('widget'))
            if w:
                with desk.lock:
                    desk.char_widgets[chat_id] = w
        text, actions = gen.generate_chat_response(chat_id, message, history)
        if not text:
            return jsonify({'messages': [], 'error': gen.no_reply_reason()})
        left = apply_habit_actions(app.habits, actions)
        resp = {'messages': [{'character': chat_id, 'text': text}]}
        if actions:
            resp['actions'] = left
            resp['habits_changed'] = len(left) != len(actions)
        return jsonify(resp)

    @web.route('/api/chat_todos', methods=['POST'])
    def chat_todos():
        data = request.json or {}
        pid = data.get('chat')
        if not app.profiles.get(pid):
            return jsonify({'error': 'unknown chat'}), 400
        w = normalize_todo(data.get('widget'))
        if w:
            with desk.lock:
                desk.char_widgets[pid] = w
        return jsonify({'ok': True})

    @web.route('/api/chat_todos_sync', methods=['POST'])
    def chat_todos_sync():
        bulk = (request.json or {}).get('by_chat', {})
        if not isinstance(bulk, dict):
            return jsonify({'error': 'bad payload'}), 400
        with desk.lock:
            for pid, wdata in bulk.items():
                w = normalize_todo(wdata) if isinstance(wdata, dict) else None
                if w and app.profiles.get(pid):
                    desk.char_widgets[pid] = w
        return jsonify({'ok': True})

    @web.route('/api/widget_event', methods=['POST'])
    def widget_event():
        data = request.json or {}
        pid = data.get('chat')
        if not app.profiles.get(pid):
            return jsonify({'error': 'unknown chat'}), 400
        with desk.lock:
            log = desk.widget_views.setdefault(pid, [])
            log.append({'ts': time.time(), 'action': data.get('action', 'view')})
            desk.widget_views[pid] = log[-50:]
        return jsonify({'ok': True})

    @web.route('/api/context')
    def get_context():
        ctx = build_live_context(app)
        members = set(app.roster.member_ids())
        return jsonify({'detected': ctx['detected'], 'dangers': ctx['dangers'], 'safety_state': ctx['safety_state'],
                        'mention_options': [{'id': a['id'], 'name': a['name'], 'icon': a['icon'],
                                             'object': a['object'], 'detected': a['id'] in members}
                                            for a in app.roster.snapshot()]})

    # ════════════════════════════════════════════════════════════════
    #  Habits
    # ════════════════════════════════════════════════════════════════
    @web.route('/api/habits/today')
    def habits_today():
        out = app.habits.today()
        out['vision'] = vision_habits.summary()
        return jsonify(out)

    @web.route('/api/habits/log', methods=['POST'])
    def habits_log():
        data = request.json or {}
        try:
            out = app.habits.log(str(data.get('habit', '')), str(data.get('action', '')),
                                 **{k: v for k, v in data.items() if k not in ('habit', 'action')})
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        return jsonify(out)

    @web.route('/api/habit_log')
    def habit_log():
        """Vision habit timeline (posture, nail biting) for the Habits page."""
        category = (request.args.get('category') or 'all').strip().lower()
        limit = min(max(int(request.args.get('limit', 40)), 1), 100)
        with desk.lock:
            events = [dict(e) for e in desk.habit_events]
            vlm, vlm_ts = dict(desk.vlm_habit_state or {}), desk.vlm_habit_ts
        if category != 'all':
            events = [e for e in events if e.get('category') == category]
        summary = vision_habits.summary()
        summary['breaks_today'] = app.habits.today()['breaks']['count']
        return jsonify({'events': events[:limit], 'summary': summary, 'vlm_state': vlm, 'vlm_ts': vlm_ts})

    @web.route('/api/daily_stats')
    def daily_stats():
        return jsonify(app.habits.today())

    @web.route('/api/preferences', methods=['GET', 'PUT'])
    @web.route('/api/habit_policy', methods=['GET', 'PUT'])
    def preferences():
        if request.method == 'PUT':
            data = request.json or {}
            # old names from the previous app version
            for old, new in (('dnd_enabled', 'quiet_enabled'), ('dnd_start_hour', 'quiet_start_hour'),
                             ('dnd_end_hour', 'quiet_end_hour')):
                if old in data:
                    data[new] = data.pop(old)
            try:
                p = app.prefs.update(**data)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            return jsonify({'ok': True, 'preferences': p})
        from ..habits.reminders import quiet_hours_now
        p = app.prefs.all()
        p['quiet_now'] = quiet_hours_now(app.prefs)
        p['min_gap_sec'] = float(app.settings.reminders.get('min_gap_sec', 600))
        t = app.habits.day_times(time.time())
        p['sunset_ts'] = t['sunset_ts']
        return jsonify(p)

    # ════════════════════════════════════════════════════════════════
    #  Event log (on this computer, data/events/)
    # ════════════════════════════════════════════════════════════════
    def day_arg():
        d = request.args.get('day') or local_day()
        return d if len(d) == 10 and d[4] == '-' and d[7] == '-' else local_day()

    @web.route('/api/log/days')
    def log_days():
        return jsonify({'days': app.event_log.days(), 'today': local_day()})

    @web.route('/api/log/events')
    def log_events():
        types = {t for t in (request.args.get('types') or '').split(',') if t} or None
        limit = min(max(int(request.args.get('limit', 200)), 1), 2000)
        return jsonify({'day': day_arg(), 'events': app.event_log.events(day_arg(), types, limit)})

    @web.route('/api/log/presence')
    def log_presence():
        return jsonify({'day': day_arg(), 'intervals': app.event_log.presence(day_arg())})

    @web.route('/api/log/detections')
    def log_detections():
        limit = min(max(int(request.args.get('limit', 30)), 1), 500)
        return jsonify({'day': day_arg(), 'detections': app.event_log.recent_detections(day_arg(), limit)})

    @web.route('/api/log/status')
    def log_status():
        return jsonify({'folder': str(app.event_log.folder), 'today': app.event_log.counts(local_day()),
                        'last_compaction': app.event_log.last_compaction, 'scheduler': app.scheduler.status(),
                        'stream': app.stream.stats(), 'camera': camera_payload()})

    @web.route('/api/log/compact', methods=['POST'])
    def log_compact():
        return jsonify(app.event_log.compact())

    # ════════════════════════════════════════════════════════════════
    #  Settings
    # ════════════════════════════════════════════════════════════════
    @web.route('/api/rules')
    def get_rules():
        cfg = {}
        if app.rules_path.is_file():
            cfg = json.loads(app.rules_path.read_text(encoding='utf-8'))
        return jsonify({
            'loaded': app.rules_loaded,
            'desk_outline': cfg.get('desk_outline', []),
            'danger_zones': cfg.get('danger_zones', dict(relationships.DANGER_ZONES)),
            'edge_proximity': cfg.get('edge_proximity', {'danger_px': relationships.EDGE_DANGER_PX,
                                                         'warn_px': relationships.EDGE_WARN_PX,
                                                         'objects': sorted(relationships.EDGE_OBJECTS)}),
            'chain_reaction': cfg.get('chain_reaction', {}),
            'temporal': cfg.get('temporal', {}),
            'available_objects': AVAILABLE_ZONE_OBJECTS,
        })

    @web.route('/api/rules', methods=['PUT'])
    def save_rules():
        data = request.json
        if not data:
            return jsonify({'error': 'Empty payload'}), 400
        changes = {k: data[k] for k in ('danger_zones', 'edge_proximity', 'chain_reaction', 'temporal') if k in data}
        if 'desk_outline' in data:
            try:
                pts = [[round(min(1.0, max(0.0, float(x))), 4), round(min(1.0, max(0.0, float(y))), 4)]
                       for x, y in (data['desk_outline'] or [])]
            except (TypeError, ValueError):
                return jsonify({'error': 'desk_outline must be a list of [x, y] points'}), 400
            if pts and len(pts) < 3:
                return jsonify({'error': 'Mark at least 3 corners of the desk'}), 400
            changes['desk_outline'] = pts[:12]
            # Remember how the phone was tilted when the desk was marked, so Lens can tell if it gets bumped.
            g = app.source.recent_gravity() if app.phone_is_camera else None
            changes['camera_reference'] = {'gravity': [round(v, 2) for v in g]} if (pts and g) else None
            app.camera_health.forget('bumped')
        app.save_rules(changes)
        return jsonify({'ok': True})

    @web.route('/api/scenarios')
    def get_scenarios():
        with desk.lock:
            lost = {k: {'last_seen_ago_sec': int(time.time() - v['last_seen']), 'alerted': v['alerted']}
                    for k, v in desk.lost_objects.items() if v.get('last_seen')}
            active = dict(desk.active_scenarios)
        return jsonify({'scenarios': active, 'lost_objects': lost,
                        'lost_items': {k: {'name': v['name']} for k, v in app.lost_items.items.items()}})

    return web
