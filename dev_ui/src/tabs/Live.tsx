// The Live tab: each object's state now, the list of changes, and one object's whole trace (click its card):
// what happened to it and why, how its pictures were smoothed into that state (the view that shows a
// flickering object right away), and the pictures kept of it.
import { useEffect, useMemo, useRef, useState } from 'react'
import { getObjectTrace, getStates, getTrace, getTracedLabels, type DevSettings, type ObjectNow,
         type ObjectTraceReply, type SmoothingTrace, type StateChange, type States } from '../api'
import { formatClock, formatSeconds, whoseWords } from '../format'
import ObjectTrace from '../ObjectTrace'
import SmoothingChart from '../SmoothingChart'
import { ErrorLine, Pill, Table } from '../ui'
import { countdown, isFlickering, keepRecent, mergeChanges, newestFirst, withPhases, type ChangeRow } from '../timeline'
import { usePoll } from '../usePoll'

const nowSec = () => Date.now() / 1000

/** The states, with `gap`: the answering server's clock minus this computer's, worked out when it answered. The
 *  times in the reply are by the server's clock (the Pi's, which may be off from this computer's), so "for 5 s"
 *  and the last minute's flips are measured from nowSec() + gap. */
type ShownStates = States & { gap: number }

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
 * changes doesn't pile up in the page. Every time is the server's (see ShownStates).
 */
function useStates(everyMs: number, keep: number) {
  const since = useRef<number | null>(null)
  const changes = useRef<StateChange[]>([])
  return usePoll<ShownStates>(async () => {
    if (since.current === null) {
      const first = await getStates(nowSec())
      since.current = first.watched_since ?? first.now
    }
    const reply = await getStates(since.current)
    const gap = reply.now - nowSec()
    changes.current = keepRecent(mergeChanges(changes.current, reply.changes), reply.now, keep)
    const newest = changes.current[changes.current.length - 1]
    if (newest && newest.ts > since.current) since.current = newest.ts
    return { ...reply, changes: changes.current, gap }
  }, everyMs)
}

/** Something about the picked object, asked again every poll and at once on a new pick. The reply says which
 *  object it's for, so an old reply isn't shown for a new pick. */
function usePicked<T>(picked: string | null, everyMs: number, load: (label: string) => Promise<T>) {
  const pickedNow = useRef(picked)
  pickedNow.current = picked
  const poll = usePoll<{ label: string | null, data: T | null }>(async () => {
    const label = pickedNow.current
    return { label, data: label === null ? null : await load(label) }
  }, everyMs)
  const { refresh } = poll
  useEffect(() => { refresh() }, [picked, refresh])
  return poll
}

/** A card per object; the whole card is a button that opens its trace. */
function NowCards(props: { states: States, now: number, flickerLimit: number, picked: string | null,
                           onPick: (label: string) => void }): JSX.Element {
  const { states, now, flickerLimit, picked, onPick } = props
  const labels = Object.keys(states.objects).sort()
  if (labels.length === 0) return <p className="muted">No objects seen yet.</p>
  return (
    <div className="cards">
      {labels.map(label => {
        const object = states.objects[label]
        return (
          <button key={label} type="button" className="card object-card" aria-label={label}
                  aria-pressed={picked === label} title="Show its trace" onClick={() => onPick(label)}>
            <span className="object-name mono">{label}</span>
            <span className="row">
              <StatePill state={object.state} />
              {isFlickering(states.changes, label, now, flickerLimit) && <Flickering limit={flickerLimit} />}
            </span>
            <span className="muted small">
              {object.since === null ? 'since: not known' : `for ${formatSeconds(now - object.since)}`}
            </span>
          </button>
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

/** One object's smoothing, drawn with the rules of the server that smoothed it (the Pi's own, from the Pi). */
function Smoothing(props: { settings: DevSettings, trace: SmoothingTrace }): JSX.Element {
  const { rules } = props.trace
  const steps = useMemo(() => props.trace.steps.slice(-props.settings.trace_pictures),
                        [props.trace.steps, props.settings.trace_pictures])
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
  const smoothing = usePicked<SmoothingTrace>(picked, everyMs, getTrace)
  const trace = usePicked<ObjectTraceReply>(picked, everyMs, getObjectTrace)
  const panel = useRef<HTMLDivElement>(null)
  // Every object the page knows of can be picked, also ones with no smoothing yet.
  const choices = useMemo(
    () => [...new Set([...labels, ...Object.keys(states.data?.objects ?? {})])].sort(),
    [labels, states.data],
  )
  const pick = (label: string) => {
    setPicked(label)
    panel.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }
  const now = nowSec() + (states.data?.gap ?? 0)          // by the server's clock

  return (
    <div className="stack">
      <div className="card">
        <h2>Now</h2>
        {states.data?.from === 'pi' && <p className="muted small">From {whoseWords(states.data, true)}</p>}
        <ErrorLine error={states.error} />
        {states.data ? <NowCards states={states.data} now={now} flickerLimit={settings.flicker_flips_per_min}
                                 picked={picked} onPick={pick} />
                     : !states.error && <p className="muted">Asking for the object states…</p>}
        <p className="muted small" style={{ marginTop: 10 }}>Click an object to see its whole trace below.</p>
      </div>

      <div className="card">
        <h2>Changes</h2>
        <p className="muted small">The newest {settings.sql_max_rows}; the Database tab has them all.</p>
        {states.data && <Changes states={states.data} now={now} flickerLimit={settings.flicker_flips_per_min} />}
      </div>

      <div className="card" ref={panel}>
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
          <h2 style={{ marginBottom: 0 }}>{picked === null ? 'Object trace' : `Trace of ${picked}`}</h2>
          <div className="row">
            <label className="small muted" htmlFor="live-object">Object to show</label>
            <select id="live-object" className="input" value={picked ?? ''} disabled={choices.length === 0}
                    onChange={e => setPicked(e.target.value || null)}>
              {picked === null && <option value="">none yet</option>}
              {picked !== null && !choices.includes(picked) && <option value={picked}>{picked}</option>}
              {choices.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
        </div>
        {/* With nothing picked, "No objects seen yet." gives way to either list's error: say which. */}
        <ErrorLine error={traced.error ?? trace.error ?? smoothing.error ?? (picked === null ? states.error : null)} />
        {picked === null
          ? !(traced.error ?? states.error) && <p className="muted">No objects seen yet.</p>
          : trace.data?.label !== picked || trace.data.data === null
            ? <p className="muted">Asking for {picked}'s trace…</p>
            : <ObjectTrace
                label={picked} settings={settings} trace={trace.data.data}
                smoothing={smoothing.data?.label !== picked || smoothing.data.data === null
                  ? <p className="muted">Asking for {picked}'s pictures…</p>
                  : <Smoothing settings={settings} trace={smoothing.data.data} />} />}
        <p className="muted small" style={{ marginTop: 10 }}>
          The smoothing shows the last {settings.trace_pictures} pictures. The page asks for new numbers every
          {' '}{settings.poll_sec} s.
        </p>
      </div>
    </div>
  )
}
