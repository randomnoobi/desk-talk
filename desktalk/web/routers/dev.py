"""
The developer page (http://localhost:5000/dev/, dev_ui/). Only there when settings → dev.enabled is true, and
only for this computer, from the server's own page or the Vite dev server's (DevOnly): anything else is a 403.

    GET  /api/dev/settings                  every number the page uses (it keeps no copies)
    GET  /api/dev/states?since=<ts>         each object's state now, and the changes after `since`
    GET  /api/dev/smoothing                 the objects with a smoothing trace
    GET  /api/dev/smoothing/trace?label=    one object's last pictures of smoothing, oldest first ([] for one
                                            never seen; the label is a query parameter, so pen/pencil works)
    GET  /api/dev/objects/<label>           one object's whole trace: its state changes since the camera
                                            started and its chat joins, leaves and refusals (oldest first,
                                            each with why), and the pictures kept of it (pen/pencil works too)
    GET  /api/dev/objects/photo/<id>        one of those pictures (a JPEG); 404 once it's gone
    GET  /api/dev/tests                     every test pytest finds
    POST /api/dev/runs                      {"check": true} or {"tests": [ids]} → {run_id}; 409 while a run is
                                            going, 400 for a test that isn't in the list
    GET  /api/dev/runs/<run_id>?from=<n>    how the run is going, with its printout after offset n
    POST /api/dev/runs/<run_id>/stop        stop it
    GET  /api/dev/db                        the databases
    GET  /api/dev/db/<name>/tables          their tables
    POST /api/dev/db/<name>/query           {"sql": "..."}: one statement that only reads; 400 for anything else
    GET  /api/dev/files                     what's in the data folder
    GET  /api/dev/files/preview?path=       the start of one file in it; 400 outside it, 404 when missing
    GET  /api/dev/scenarios                 the Demo tab's pretend warnings, and the ones counting down
    POST /api/dev/scenarios                 {"name": ..., "after_sec": n}: start one counting down; 400 for a name
                                            not in the list, a countdown out of range, or too many counting down
    DELETE /api/dev/scenarios/<id>          stop one counting down; 404 when it isn't (any more)

The Demo tab is the one place this page changes what the app does on purpose: once its countdown is over, a
pretend warning goes out the way a real one does (the chat, the Mac pop-up, the event log; a pretend sip counts).

The errors are the parts' own words from config/prompts.yaml → dev.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response

from desktalk.domain import ChatJoin, Phase, QueryRefused, RunAlreadyGoing, StateRecord
from desktalk.ports import DatabaseBrowser, DataFiles, DemoScenarios, TestRunner
from desktalk.web.deps import App, DevOnly
from desktalk.web.schemas import QueryRequest, RunRequest, ScenarioRequest

router = APIRouter(dependencies=[DevOnly])


def _words(error: Exception) -> str:
    """What an error says: a KeyError's own message (str() of a KeyError adds quotes)."""
    return str(error.args[0]) if isinstance(error, KeyError) and error.args else str(error)


def _runner(app: App) -> TestRunner:
    if app.test_runner is None:         # the developer page is off (then this router isn't added at all)
        raise HTTPException(404)
    return app.test_runner


def _databases(app: App) -> DatabaseBrowser:
    if app.databases is None:
        raise HTTPException(404)
    return app.databases


def _data_files(app: App) -> DataFiles:
    if app.data_files is None:
        raise HTTPException(404)
    return app.data_files


def _demo(app: App) -> DemoScenarios:
    if app.demo is None:
        raise HTTPException(404)
    return app.demo


def _cell(value: Any) -> Any:
    """A database value as JSON can carry it: bytes as an SQL blob (x'00ff'), and a number too big for JSON
    (inf) as text."""
    if isinstance(value, bytes):
        return f"x'{value.hex()}'"
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


@router.get('/api/dev/settings')
def dev_settings(app: App) -> dict[str, Any]:
    config = app.config
    dev, states = config.dev, config.object_states
    return {
        'poll_sec': dev.poll_sec,
        'checks': [check.name for check in dev.checks],
        'health': dev.health.model_dump(mode='json'),
        'trace_pictures': states.trace_pictures,
        'sql_max_rows': dev.sql_max_rows,
        'data_folder': str(config.resolve(config.storage.folder)),
        'object_states': {'on_confidence': states.on_confidence, 'off_confidence': states.off_confidence,
                          'appear_sec': states.appear_sec, 'gone_sec': states.gone_sec,
                          'window_pictures': states.window_pictures, 'vote_share': states.vote_share},
        'disconnect_after_sec': config.camera.disconnect_after_sec,
        'web_port': config.web.port,
        'flicker_flips_per_min': dev.flicker_flips_per_min,
        'output_keep_kb': dev.output_keep_kb,
        'flips_window_min': dev.flips_window_min,
        'table_counts_every_sec': dev.table_counts_every_sec,
        'trace_events_kept': dev.trace_events_kept,
        'trace_photos_kept': dev.trace_photos_kept,
        'scenarios': {'countdown_default_sec': dev.scenarios.countdown_default_sec,
                      'countdown_max_sec': dev.scenarios.countdown_max_sec,
                      'waiting_max': dev.scenarios.waiting_max},
    }


@router.get('/api/dev/states')
def dev_states(app: App, since: float = 0.0) -> dict[str, Any]:
    snapshot = app.object_states.snapshot()
    return {
        'objects': {label: {'state': seen.state, 'since': seen.since} for label, seen in snapshot.objects.items()},
        'watched_since': snapshot.watched_since,
        'changes': [asdict(change) for change in app.object_states.changes(since)],
    }


@router.get('/api/dev/smoothing')
def dev_smoothing(app: App) -> dict[str, Any]:
    return {'labels': app.object_states.traced_labels()}


@router.get('/api/dev/smoothing/trace')
def dev_smoothing_trace(app: App, label: str) -> dict[str, Any]:
    return {'label': label, 'steps': [asdict(step) for step in app.object_states.trace(label)]}


def _state_row(record: StateRecord) -> dict[str, Any]:
    """One object state change on the trace's timeline. `seen_for_sec` is how long it had been seen, so only a
    change out of PRESENT has one (its dwell_sec is the time it was PRESENT)."""
    seen_for = record.dwell_sec if record.prev_state == Phase.PRESENT else None
    return {'ts': record.ts, 'source': 'state', 'kind': record.type, 'confidence': record.conf_avg,
            'seen_for_sec': seen_for, 'on_desk': None, 'step': None, 'before': str(record.prev_state),
            'after': str(record.new_state)}


def _chat_row(join: ChatJoin) -> dict[str, Any]:
    """One chat join, leave or refusal on the trace's timeline."""
    return {'ts': join.ts, 'source': 'chat', 'kind': join.kind, 'confidence': join.confidence,
            'seen_for_sec': join.seen_for_sec, 'on_desk': join.on_desk, 'step': join.step, 'before': None,
            'after': None}


# Before the trace route: its {label:path} would take "photo/<id>" too.
@router.get('/api/dev/objects/photo/{photo_id}')
def dev_object_photo(app: App, photo_id: str) -> Response:
    jpeg = app.trace_photos.jpeg(photo_id) if app.trace_photos is not None else None
    if jpeg is None:
        raise HTTPException(404)                 # only the newest are kept, or the developer page is off
    return Response(jpeg, media_type='image/jpeg')


@router.get('/api/dev/objects/{label:path}')
def dev_object_trace(app: App, label: str) -> dict[str, Any]:
    """The changes since the camera's current run of pictures began (like the Live tab's list), the newest
    settings → dev.trace_events_kept of them; the chat's own list is kept that short already. Before the first
    picture since the app started there are none: the store's changes are all from earlier runs."""
    since = app.object_states.snapshot().watched_since
    changes = [] if since is None else app.state_store.changes(since, label=label)[-app.config.dev.trace_events_kept:]
    joins = app.chat_joins.joins(label) if app.chat_joins is not None else []
    timeline = sorted([*map(_state_row, changes), *map(_chat_row, joins)], key=lambda row: row['ts'])
    photos = app.trace_photos.photos(label) if app.trace_photos is not None else []
    return {'label': label, 'timeline': timeline,
            'photos': [{'id': p.id, 'ts': p.ts, 'why': p.why, 'confidence': p.confidence, 'box': list(p.box),
                        'size': list(p.size)} for p in photos]}


@router.get('/api/dev/tests')
def dev_tests(app: App) -> dict[str, Any]:
    try:
        return {'tests': [asdict(item) for item in _runner(app).collect()]}
    except RuntimeError as error:           # the test command can't run (a missing program, a test file stuck)
        raise HTTPException(503, str(error)) from None


@router.post('/api/dev/runs')
def dev_start_run(app: App, body: RunRequest) -> dict[str, Any]:
    runner = _runner(app)
    try:
        if body.check:
            run_id = runner.start_check()
        elif body.tests is not None:
            run_id = runner.start_tests(body.tests)
        else:
            raise HTTPException(400, app.config.prompts.dev.nothing_to_run)
    except RunAlreadyGoing as error:
        raise HTTPException(409, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return {'run_id': run_id}


@router.get('/api/dev/runs/{run_id}')
def dev_run_status(app: App, run_id: str,
                   output_from: Annotated[int, Query(alias='from', ge=0)] = 0) -> dict[str, Any]:
    try:
        return asdict(_runner(app).status(run_id, output_from))
    except KeyError as error:
        raise HTTPException(404, _words(error)) from None


@router.post('/api/dev/runs/{run_id}/stop')
def dev_stop_run(app: App, run_id: str) -> dict[str, Any]:
    _runner(app).stop(run_id)
    return {'ok': True}


@router.get('/api/dev/db')
def dev_databases(app: App) -> dict[str, Any]:
    return {'databases': _databases(app).databases()}


@router.get('/api/dev/db/{name}/tables')
def dev_tables(app: App, name: str) -> dict[str, Any]:
    try:
        return {'tables': [asdict(table) for table in _databases(app).tables(name)]}
    except KeyError as error:
        raise HTTPException(404, _words(error)) from None


@router.post('/api/dev/db/{name}/query')
def dev_query(app: App, name: str, body: QueryRequest) -> dict[str, Any]:
    try:
        result = _databases(app).query(name, body.sql)
    except KeyError as error:
        raise HTTPException(404, _words(error)) from None
    except QueryRefused as error:
        raise HTTPException(400, str(error)) from None
    return {**asdict(result), 'rows': [[_cell(value) for value in row] for row in result.rows]}


@router.get('/api/dev/files')
def dev_files(app: App) -> dict[str, Any]:
    return {'files': [asdict(found) for found in _data_files(app).list()]}


@router.get('/api/dev/files/preview')
def dev_file_preview(app: App, path: str) -> dict[str, Any]:
    try:
        return {'path': path, 'text': _data_files(app).preview(path)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from None


@router.get('/api/dev/scenarios')
def dev_scenarios(app: App) -> dict[str, Any]:
    demo = _demo(app)
    return {'scenarios': [asdict(s) for s in demo.scenarios()], 'waiting': [asdict(w) for w in demo.waiting()]}


@router.post('/api/dev/scenarios')
def dev_start_scenario(app: App, body: ScenarioRequest) -> dict[str, Any]:
    try:
        return asdict(_demo(app).start(body.name, body.after_sec))
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@router.delete('/api/dev/scenarios/{scenario_id}')
def dev_cancel_scenario(app: App, scenario_id: str) -> dict[str, Any]:
    try:
        _demo(app).cancel(scenario_id)
    except KeyError as error:
        raise HTTPException(404, _words(error)) from None
    return {'ok': True}
