// The Live tab: each object's state now, the list of changes, and how one object's pictures were
// smoothed into that state (the view that shows a flickering object right away).
import { useEffect, useMemo, useRef, useState } from 'react'
import { getStates, getTrace, getTracedLabels, type DevSettings, type ObjectNow, type SmoothingStep,
         type StateChange, type States } from '../api'
import { formatClock, formatSeconds } from '../format'
import SmoothingChart from '../SmoothingChart'
import { ErrorLine, Pill, Table } from '../ui'
import { countdown, isFlickering, keepRecent, mergeChanges, newestFirst, withPhases, type ChangeRow } from '../timeline'
import { usePoll } from '../usePoll'

const nowSec = () => Date.now() / 1000

/** PRESENT green, ABSENT grey, UNKNOWN an outline. */
function StatePill(props: { state: ObjectNow['state'] }): JSX.Element {
  if (props.state === 'PRESENT') return <Pill level="ok">PRESENT</Pill>
  if (props.state === 'ABSENT') return <Pill level="off">ABSENT</Pill>
  return <span className="pill" style={{ background: 'transparent', boxShadow: 'inset 0 0 0 1.5px var(--ink-3)' }}>UNKNOWN</span>
}

const Flickering = (props: { limit: number }) =>
  <Pill level="warn" title={`More than ${props.limit} flips in the last minute`}>flickering</Pill>

/**
 * Asks for the states, and only for the changes it hasn't got yet. The first time it asks for the
 * changes since the camera's current run of pictures began (or since the page opened, if that isn't known).
 * It keeps the newest `keep` changes, plus all of the last minute's for the flickering badge, so a day of
 * changes doesn't pile up in the page.
 */
function useStates(everyMs: number, keep: number) {
  const since = useRef<number | null>(null)
  const changes = useRef<StateChange[]>([])
  return usePoll<States>(async () => {
    if (since.current === null) {
      const first = await getStates(nowSec())
      since.current = first.watched_since ?? nowSec()
    }
    const reply = await getStates(since.current)
    changes.current = keepRecent(mergeChanges(changes.current, reply.changes), nowSec(), keep)
    const newest = changes.current[changes.current.length - 1]
    if (newest && newest.ts > since.current) since.current = newest.ts
    return { ...reply, changes: changes.current }
  }, everyMs)
}

/** The trace of the picked object. The reply says which object it's for, so an old reply isn't shown for a new pick. */
function useTrace(picked: string | null, everyMs: number) {
  const pickedNow = useRef(picked)
  pickedNow.current = picked
  const poll = usePoll<{ label: string | null, steps: SmoothingStep[] }>(async () => {
    const label = pickedNow.current
    return { label, steps: label === null ? [] : await getTrace(label) }
  }, everyMs)
  const { refresh } = poll
  useEffect(() => { refresh() }, [picked, refresh])
  return poll
}

function NowCards(props: { states: States, now: number, flickerLimit: number, traced: string[],
                           onPick: (label: string) => void }): JSX.Element {
  const { states, now, flickerLimit, traced, onPick } = props
  const labels = Object.keys(states.objects).sort()
  if (labels.length === 0) return <p className="muted">No objects seen yet.</p>
  return (
    <div className="cards">
      {labels.map(label => {
        const object = states.objects[label]
        return (
          <div key={label} className="card" role="group" aria-label={label}>
            <h3 className="mono" style={{ wordBreak: 'break-word' }}>{label}</h3>
            <div className="row">
              <StatePill state={object.state} />
              {isFlickering(states.changes, label, now, flickerLimit) && <Flickering limit={flickerLimit} />}
            </div>
            <p className="muted small" style={{ marginTop: 6 }}>
              {object.since === null ? 'since: not known' : `for ${formatSeconds(now - object.since)}`}
            </p>
            {traced.includes(label) && (
              <button type="button" className="btn" style={{ marginTop: 8 }} onClick={() => onPick(label)}>
                Show its smoothing
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}

function Changes(props: { states: States, now: number, flickerLimit: number }): JSX.Element {
  const { states, now, flickerLimit } = props
  const [only, setOnly] = useState('')
  const rows = useMemo(() => newestFirst(withPhases(states.changes)), [states.changes])
  const labels = useMemo(
    () => [...new Set([...Object.keys(states.objects), ...states.changes.map(c => c.label)])].sort(),
    [states.objects, states.changes],
  )
  const shown = only ? rows.filter(r => r.label === only) : rows
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <label className="small muted" htmlFor="live-changes-for">Show changes for</label>
        <select id="live-changes-for" className="input" value={only} onChange={e => setOnly(e.target.value)}>
          <option value="">every object</option>
          {labels.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>
      <Table<ChangeRow>
        rows={shown}
        rowKey={r => `${r.ts}|${r.label}|${r.change}`}
        empty={states.watched_since === null
          ? 'No changes yet.'
          : `No changes since the camera started (${formatClock(states.watched_since)}).`}
        columns={[
          { label: 'Time', cell: r => <span className="mono">{formatClock(r.ts)}</span> },
          { label: 'Object', cell: r => (
            <span className="row">
              <span className="mono">{r.label}</span>
              {isFlickering(states.changes, r.label, now, flickerLimit) && <Flickering limit={flickerLimit} />}
            </span>
          ) },
          { label: 'Change', cell: r => r.change },
          { label: 'Before → after', cell: r => `${r.before} → ${r.after}` },
        ]}
      />
    </>
  )
}

function Smoothing(props: { settings: DevSettings, steps: SmoothingStep[] }): JSX.Element {
  const rules = props.settings.object_states
  const steps = useMemo(() => props.steps.slice(-props.settings.trace_pictures), [props.steps, props.settings.trace_pictures])
  if (steps.length === 0) return <p className="muted">No pictures yet for this object</p>
  const last = steps[steps.length - 1]
  const wait = countdown(last, rules)
  return (
    <div className="stack">
      {wait && (
        <div className="row">
          <Pill level="warn">{`Waiting to turn ${wait.heading}: ${formatSeconds(wait.waited)} of ${formatSeconds(wait.of)}`}</Pill>
          <progress max={wait.of} value={Math.min(wait.waited, wait.of)} aria-label="How long it has waited" />
        </div>
      )}
      <SmoothingChart steps={steps} rules={rules} />
      <p className="small muted">
        Top: the box's confidence for each picture (gaps: no box), with the on line at {rules.on_confidence} and
        the off line at {rules.off_confidence}. <strong>on/off</strong>: blue while those two lines say
        "seen". <strong>vote</strong>: the share of the last {rules.window_pictures} pictures that said yes,
        green once it's more than the dashed mark at {rules.vote_share}. <strong>state</strong>: PRESENT
        green, ABSENT grey, UNKNOWN an outline, with a mustard line while it waits ({rules.appear_sec} s to
        appear, {rules.gone_sec} s to go).
      </p>
      <p className="small">
        Last picture ({formatClock(last.ts)}): confidence {last.conf === null ? 'no box' : last.conf.toFixed(2)},
        on/off says {last.box_seen ? 'seen' : 'not seen'}, vote {last.votes_yes} of {last.votes_total}
        ({last.seen ? 'seen' : 'not seen'}), state {last.phase}.
      </p>
    </div>
  )
}

export default function Live(props: { settings: DevSettings }): JSX.Element {
  const { settings } = props
  const everyMs = settings.poll_sec * 1000
  // The list keeps as many changes as the Database tab's "Latest changes" query shows (settings.sql_max_rows).
  const states = useStates(everyMs, settings.sql_max_rows)
  const traced = usePoll(getTracedLabels, everyMs)
  const labels = useMemo(() => traced.data ?? [], [traced.data])
  const [picked, setPicked] = useState<string | null>(null)
  useEffect(() => {
    if (picked === null && labels.length > 0) setPicked(labels[0])
  }, [picked, labels])
  const trace = useTrace(picked, everyMs)
  const now = nowSec()

  return (
    <div className="stack">
      <div className="card">
        <h2>Now</h2>
        <ErrorLine error={states.error} />
        {states.data ? <NowCards states={states.data} now={now} flickerLimit={settings.flicker_flips_per_min}
                                 traced={labels} onPick={setPicked} />
                     : !states.error && <p className="muted">Asking for the object states…</p>}
      </div>

      <div className="card">
        <h2>Changes</h2>
        <p className="muted small">The newest {settings.sql_max_rows}; the Database tab has them all.</p>
        {states.data && <Changes states={states.data} now={now} flickerLimit={settings.flicker_flips_per_min} />}
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
          <h2 style={{ marginBottom: 0 }}>Smoothing</h2>
          <div className="row">
            <label className="small muted" htmlFor="live-object">Object to show</label>
            <select id="live-object" className="input" value={picked ?? ''} disabled={labels.length === 0}
                    onChange={e => setPicked(e.target.value || null)}>
              {picked === null && <option value="">none yet</option>}
              {picked !== null && !labels.includes(picked) && <option value={picked}>{picked}</option>}
              {labels.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
        </div>
        <ErrorLine error={traced.error ?? trace.error} />
        {picked === null
          ? <p className="muted">No objects seen yet.</p>
          : trace.data?.label !== picked
            ? <p className="muted">Asking for {picked}'s pictures…</p>
            : <Smoothing settings={settings} steps={trace.data.steps} />}
        <p className="muted small" style={{ marginTop: 10 }}>
          The last {settings.trace_pictures} pictures. The page asks for new numbers every {settings.poll_sec} s.
        </p>
      </div>
    </div>
  )
}
