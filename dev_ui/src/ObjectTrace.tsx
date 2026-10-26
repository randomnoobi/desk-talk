// One object's whole trace (click an object on the Live tab): what happened to it and why, its smoothing, and
// the pictures kept of it with its box drawn on. The numbers come from GET /api/dev/objects/{label}.
import { useMemo, type ReactNode } from 'react'
import { tracePhotoUrl, type DevSettings, type ObjectTraceReply, type TracePhoto, type TraceRow } from './api'
import { formatClock, formatSeconds } from './format'
import { Table } from './ui'

const STATE_WORDS: Record<string, string> = { appeared: 'appeared', removed: 'went away', moved: 'moved' }
const CHAT_WORDS: Record<string, string> = {
  joined: 'joined the chat', left: 'left the chat', refused: 'refused: not on the chat list',
}
const PHOTO_WORDS: Record<TracePhoto['why'], string> = {
  appeared: 'when it appeared', joined: 'when it joined the chat', every: 'while it was there',
}

/** What happened, in plain words: "went away", "refused: not on the chat list". */
export function whatHappened(row: TraceRow): string {
  return (row.source === 'chat' ? CHAT_WORDS : STATE_WORDS)[row.kind] ?? row.kind
}

/** Why, in plain words: "seen for 3.2 s, 0.81 sure, inside the desk, sensitivity Normal". */
export function whyWords(row: TraceRow): string {
  const parts: string[] = []
  if (row.seen_for_sec !== null) parts.push(`seen for ${formatSeconds(row.seen_for_sec)}`)
  if (row.confidence !== null) {
    parts.push(`${row.confidence.toFixed(2)} sure${row.source === 'state' ? ' on average' : ''}`)
  }
  if (row.on_desk !== null) parts.push(row.on_desk ? 'inside the desk' : 'outside the desk')
  else if (row.source === 'chat') parts.push('no desk marked')
  if (row.step !== null) parts.push(`sensitivity ${row.step}`)
  if (row.before !== null && row.after !== null) parts.push(`${row.before} → ${row.after}`)
  return parts.join(', ')
}

/** A picture with the object's box drawn on, in the picture's own pixels (the SVG stretches with the picture). */
function Photo(props: { label: string, photo: TracePhoto }): JSX.Element {
  const { label, photo } = props
  const [width, height] = photo.size
  const [x1, y1, x2, y2] = photo.box
  const why = PHOTO_WORDS[photo.why] ?? photo.why
  return (
    <figure className="trace-photo">
      <div className="trace-photo-frame">
        <img src={tracePhotoUrl(photo.id)} alt={`${label}, ${why}`} width={width} height={height} loading="lazy" />
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
          <rect x={x1} y={y1} width={x2 - x1} height={y2 - y1} />
        </svg>
      </div>
      <figcaption className="small">
        <span className="mono">{formatClock(photo.ts)}</span> {why}, {photo.confidence.toFixed(2)} sure
      </figcaption>
    </figure>
  )
}

export default function ObjectTrace(props: { label: string, settings: DevSettings, trace: ObjectTraceReply,
                                             smoothing: ReactNode }): JSX.Element {
  const { label, settings, trace, smoothing } = props
  const rows = useMemo(() => [...trace.timeline].reverse(), [trace.timeline])
  return (
    <section className="stack" aria-label={`Trace of ${label}`}>
      <div>
        <h3>What happened</h3>
        <Table<TraceRow>
          rows={rows}
          rowKey={(r, i) => `${r.ts}|${r.source}|${r.kind}|${i}`}
          empty="Nothing has happened to it since the camera started."
          columns={[
            { label: 'Time', cell: r => <span className="mono">{formatClock(r.ts)}</span> },
            { label: 'Where', cell: r => (r.source === 'chat' ? 'the chat' : 'the desk') },
            { label: 'What happened', cell: whatHappened },
            { label: 'Why', cell: whyWords },
          ]}
        />
        <p className="muted small" style={{ marginTop: 6 }}>
          Newest first: the newest {settings.trace_events_kept} changes since the camera started, and the newest
          {' '}{settings.trace_events_kept} times it joined, left or was refused from the chat.
        </p>
      </div>
      <div>
        <h3>Smoothing</h3>
        {smoothing}
      </div>
      <div>
        <h3>Pictures</h3>
        {trace.photos.length === 0
          ? <p className="muted">No pictures of it yet.</p>
          : <div className="trace-photos">{trace.photos.map(p => <Photo key={p.id} label={label} photo={p} />)}</div>}
        <p className="muted small" style={{ marginTop: 6 }}>
          One when it appears, one when it joins the chat, and one every so often while it's there. Only the
          newest {settings.trace_photos_kept} are kept.
        </p>
      </div>
    </section>
  )
}
