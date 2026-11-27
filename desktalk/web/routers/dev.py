"""
The developer page (http://localhost:5000/dev/, dev_ui/). Only there when settings → dev.enabled is true, and
only for this computer, from the server's own page or the Vite dev server's (DevOnly): anything else is a 403.

    GET  /api/dev/settings                  every number the page uses (it keeps no copies)

  The Live tab's five routes answer from the machine that sees the objects: on a computer whose camera is the Pi
  (settings: pi_camera_elsewhere), the Pi's own GET /api/objects/... (routers/objects.py), fetched from
  dev.pi_address, with "from": "pi" and "address"; otherwise this server's own, with "from": "here" and
  "address": "". 502 when the Pi doesn't answer. (The photo route stays a JPEG, with no "from".)

    GET  /api/dev/states?since=<ts>         each object's state now, the changes after `since`, and `now` (the
                                            time by the clock of the machine that answered)
    GET  /api/dev/smoothing                 the objects with a smoothing trace
    GET  /api/dev/smoothing/trace?label=    one object's last pictures of smoothing, oldest first ([] for one
                                            never seen; the label is a query parameter, so pen/pencil works), and
                                            the `rules` that machine smooths with (the chart's lines and waits)
    GET  /api/dev/objects/<label>           one object's whole trace: its state changes since the camera
                                            started and its chat joins, leaves and refusals (oldest first,
                                            each with why and the picture taken for it), and the pictures kept
                                            of it (pen/pencil works too). The same story the app page shows
                                            (desktalk/web/stories.py), but an empty one for a word never seen
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
    GET  /api/dev/pi_stats?since=<ts>       the Pi's system numbers (its GET /api/system/stats, fetched from
                                            dev.pi_address, since the page can't ask the Pi itself), with
                                            "address" added; 502 when the Pi doesn't answer
    GET  /api/dev/health                    the System tab's health checks and Pi cards, and the Storage tab's
                                            Pi card: on a computer whose camera is the Pi (settings →
                                            web.public_address empty), the Pi's own GET /api/health, fetched
                                            from dev.pi_address, with "from": "pi" and "address"; otherwise this
                                            server's own, with "from": "here" and "address": "". 502 when the Pi
                                            doesn't answer. (The rest of the System tab reads this computer's
                                            own GET /api/health and GET /api/log/status.)
    GET  /api/dev/pi_log?source=&after=<n>  the Pi log tab: the lines of one of the Pi's logs numbered after n
                                            (0: all it kept), and how the ssh connection is doing. Asking starts
                                            ssh (settings → dev.pi_log) and keeps it going; 404 for a log not in
                                            the settings

The Demo tab is the one place this page changes what the app does on purpose: once its countdown is over, a
pretend warning goes out the way a real one does (the chat, the Mac pop-up, the event log; a pretend sip counts).

The errors are the parts' own words from config/prompts.yaml → dev.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response

from desktalk.domain import QueryRefused, RunAlreadyGoing, StatsUnavailable
from desktalk.ports import DatabaseBrowser, DataFiles, DemoScenarios, PiLog, PiStatsLink, TestRunner
from desktalk.web.deps import App, DevOnly
from desktalk.web.routers.health import health as own_health
from desktalk.web.routers.objects import smoothing as object_smoothing
from desktalk.web.routers.objects import smoothing_rules
from desktalk.web.routers.objects import smoothing_trace as object_smoothing_trace
from desktalk.web.routers.objects import states as object_states
from desktalk.web.schemas import QueryRequest, RunRequest, ScenarioRequest
from desktalk.web.stories import object_story, story_photo

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


def _pi_stats_link(app: App) -> PiStatsLink:
    if app.pi_stats_link is None:
        raise HTTPException(404)
    return app.pi_stats_link


def _pi_elsewhere(app: App) -> PiStatsLink | None:
    """The line to the Pi when this is a computer whose camera is the Pi (settings: pi_camera_elsewhere), so the Pi's
    own server knows how its camera and the rest are doing; None when this server knows itself."""
    return app.pi_stats_link if app.config.pi_camera_elsewhere else None


def _pi_unreachable(app: App, unreachable: str, error: StatsUnavailable) -> HTTPException:
    """A 502 with `unreachable`'s words (prompts.yaml → dev, with {address} and {problem})."""
    return HTTPException(502, unreachable.format(address=app.config.dev.pi_address, problem=str(error)))


def _ask_pi(app: App, ask: Callable[[], dict[str, Any]], unreachable: str) -> dict[str, Any]:
    """`ask()`'s reply from the Pi, with its address; a 502 with `unreachable`'s words when the Pi doesn't answer
    or sends something else."""
    try:
        reply = ask()
    except StatsUnavailable as error:
        raise _pi_unreachable(app, unreachable, error) from None
    return {**reply, 'address': app.config.dev.pi_address}


def _live(app: App, ask_pi: Callable[[PiStatsLink], dict[str, Any]],
          here: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """A Live tab reply from the machine that sees the objects: the Pi when its camera is elsewhere (`ask_pi`),
    else this server (`here`), each saying where it came from, as /api/dev/health does."""
    link = _pi_elsewhere(app)
    if link is None:
        return {**here(), 'from': 'here', 'address': ''}
    return {**_ask_pi(app, lambda: ask_pi(link), app.config.prompts.dev.pi_objects_unreachable), 'from': 'pi'}


def _pi_log(app: App) -> PiLog:
    if app.pi_log is None:
        raise HTTPException(404)
    return app.pi_log


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
    dev, states, story = config.dev, config.object_states, config.object_story
    return {
        'poll_sec': dev.poll_sec,
        'checks': [check.name for check in dev.checks],
        'health': dev.health.model_dump(mode='json'),
        'pi_hot': {'warning_c': config.pi.hot_warning_c, 'serious_c': config.pi.hot_serious_c},   # the Pi tab
        'trace_pictures': states.trace_pictures,
        'sql_max_rows': dev.sql_max_rows,
        'data_folder': str(app.data_folder),            # the folder in memory when nothing is saved
        'object_states': smoothing_rules(app),          # this server's (a Pi's trace brings the Pi's own)
        'disconnect_after_sec': config.camera.disconnect_after_sec,
        'web_port': config.web.port,
        'flicker_flips_per_min': dev.flicker_flips_per_min,
        'output_keep_kb': dev.output_keep_kb,
        'flips_window_min': dev.flips_window_min,
        'table_counts_every_sec': dev.table_counts_every_sec,
        'trace_events_kept': story.events_kept,
        'trace_photos_kept': story.timed_photos_kept,
        'trace_step_photos_kept': story.step_photos_kept,
        'scenarios': {'countdown_default_sec': dev.scenarios.countdown_default_sec,
                      'countdown_max_sec': dev.scenarios.countdown_max_sec,
                      'waiting_max': dev.scenarios.waiting_max},
        'pi_log': {'sources': [{'name': source.name, 'label': source.label} for source in dev.pi_log.sources],
                   'keep_lines': dev.pi_log.keep_lines},
    }


# The Live tab's routes are plain defs, so FastAPI runs them in a thread: the Pi may take up to
# dev.pi_stats_timeout_sec to answer.
@router.get('/api/dev/states')
def dev_states(app: App, since: float = 0.0) -> dict[str, Any]:
    return _live(app, lambda link: link.states(since), lambda: object_states(app, since))


@router.get('/api/dev/smoothing')
def dev_smoothing(app: App) -> dict[str, Any]:
    return _live(app, lambda link: link.traced_labels(), lambda: object_smoothing(app))


@router.get('/api/dev/smoothing/trace')
def dev_smoothing_trace(app: App, label: str) -> dict[str, Any]:
    return _live(app, lambda link: link.trace(label), lambda: object_smoothing_trace(app, label))


# Before the trace route: its {label:path} would take "photo/<id>" too.
@router.get('/api/dev/objects/photo/{photo_id}')
def dev_object_photo(app: App, photo_id: str) -> Response:
    """The picture from the machine that took it: the Pi when its camera is elsewhere (404 once it's gone there
    too), else this server."""
    link = _pi_elsewhere(app)
    if link is None:
        return story_photo(app, photo_id)
    try:
        jpeg = link.photo(photo_id)
    except StatsUnavailable as error:
        raise _pi_unreachable(app, app.config.prompts.dev.pi_objects_unreachable, error) from None
    if jpeg is None:
        raise HTTPException(404)                 # the Pi keeps only the newest
    return Response(jpeg, media_type='image/jpeg')


@router.get('/api/dev/objects/{label:path}')
def dev_object_trace(app: App, label: str) -> dict[str, Any]:
    return _live(app, lambda link: link.story(label), lambda: object_story(app, label))


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


@router.get('/api/dev/pi_stats')
def dev_pi_stats(app: App, since: float | None = None) -> dict[str, Any]:
    """A plain def, so FastAPI runs it in a thread: the Pi may take up to dev.pi_stats_timeout_sec to answer."""
    link = _pi_stats_link(app)
    return _ask_pi(app, lambda: link.fetch(since), app.config.prompts.dev.pi_stats_unreachable)


@router.get('/api/dev/health')
def dev_health(app: App) -> dict[str, Any]:
    """How every part is doing, from the machine that knows: the Pi when it is elsewhere, else this server, each
    saying where it came from. A plain def, so FastAPI runs it in a thread: the Pi may take up to
    dev.pi_stats_timeout_sec to answer."""
    link = _pi_elsewhere(app)
    if link is None:
        return {**own_health(app), 'from': 'here', 'address': ''}
    return {**_ask_pi(app, link.health, app.config.prompts.dev.pi_health_unreachable), 'from': 'pi'}


@router.get('/api/dev/pi_log')
def dev_pi_log(app: App, source: str, after: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    try:
        return asdict(_pi_log(app).read(source, after))
    except KeyError as error:
        raise HTTPException(404, _words(error)) from None
