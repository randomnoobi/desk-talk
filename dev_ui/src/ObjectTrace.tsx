// One object's whole trace (click an object on the Live tab): what happened to it and why, each step with the
// picture taken for it (its box drawn on), and its smoothing. The numbers come from GET /api/dev/objects/{label}.
import { useMemo, useState, type ReactNode } from 'react'
import { tracePhotoUrl, type DevSettings, type ObjectTraceReply, type TracePhoto, type TraceRow } from './api'
import { formatClock, formatSeconds } from './format'
import { Table } from './ui'

const STATE_WORDS: Record<string, string> = { appeared: 'appeared', removed: 'went away', moved: 'moved' }
const CHAT_WORDS: Record<string, string> = {
  joined: 'joined the chat', left: 'left the chat', refused: 'refused: not on the chat list',
}
const PHOTO_WORDS: Record<TracePhoto['why'], string> = {
  appeared: 'when it appeared', joined: 'when it joined the chat', moved: 'when it moved',
  removed: 'when it went away', every: 'while it was there',
}

/** What happened, in plain words: "went away", "refused: not on the chat list", "a picture while it was there". */
export function whatHappened(row: TraceRow): string {
  if (row.source === 'photo') {
    const when = PHOTO_WORDS[row.kind as TracePhoto['why']]
    return when ? `a picture ${when}` : row.what || row.kind
  }
  return (row.source === 'chat' ? CHAT_WORDS : STATE_WORDS)[row.kind] ?? (row.what || row.kind)
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
function Picture(props: { label: string, photo: TracePhoto }): JSX.Element {
  const { label, photo } = props
  const [width, height] = photo.size
  const [x1, y1, x2, y2] = photo.box
  return (
    <div className="trace-photo-frame">
      <img src={tracePhotoUrl(photo.id)} alt={`${label}, ${PHOTO_WORDS[photo.why] ?? photo.why}`} width={width}
           height={height} loading="lazy" />
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
        <rect x={x1} y={y1} width={x2 - x1} height={y2 - y1} />
      </svg>
    </div>
  )
}

/** The small picture in a step's row: a button that opens it big. */
function Thumbnail(props: { label: string, photo: TracePhoto, onOpen: (photo: TracePhoto) => void }): JSX.Element {
  const { label, photo, onOpen } = props
  return (
    <button type="button" className="trace-thumb" onClick={() => onOpen(photo)}
            title="Show this picture bigger" aria-label={`Show the picture of ${label} bigger`}>
      <Picture label={label} photo={photo} />
    </button>
  )
}

/** One picture, big, with when, why and how sure. */
function BigPicture(props: { label: string, photo: TracePhoto, onClose: () => void }): JSX.Element {
  const { label, photo, onClose } = props
  return (
    <figure className="trace-photo trace-photo-big" role="dialog" aria-label={`Picture of ${label}`}>
      <Picture label={label} photo={photo} />
      <figcaption className="small">
        <span className="mono">{formatClock(photo.ts)}</span> {PHOTO_WORDS[photo.why] ?? photo.why},
        {' '}{photo.confidence.toFixed(2)} sure
        {' '}<button type="button" className="link" onClick={onClose}>Close</button>
      </figcaption>
    </figure>
  )
}

export default function ObjectTrace(props: { label: string, settings: DevSettings, trace: ObjectTraceReply,
                                             smoothing: ReactNode }): JSX.Element {
  const { label, settings, trace, smoothing } = props
  const rows = useMemo(() => [...trace.timeline].reverse(), [trace.timeline])
  const [shown, setShown] = useState<TracePhoto | null>(null)
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
            { label: 'Where', cell: r => ({ chat: 'the chat', state: 'the desk', photo: 'the camera' })[r.source] },
            { label: 'What happened', cell: whatHappened },
            { label: 'Why', cell: whyWords },
            { label: 'Picture',
              cell: r => (r.photo ? <Thumbnail label={label} photo={r.photo} onOpen={setShown} /> : null) },
          ]}
        />
        {shown && <BigPicture label={label} photo={shown} onClose={() => setShown(null)} />}
        <p className="muted small" style={{ marginTop: 6 }}>
          Newest first: the newest {settings.trace_events_kept} changes since the camera started, and the newest
          {' '}{settings.trace_events_kept} times it joined, left or was refused from the chat. A picture is taken
          when it appears, joins the chat, moves or goes away (the empty spot, with its last box), and every so
          often while it's there. Only the newest {settings.trace_photos_kept} timed pictures and, apart from
          them, the newest {settings.trace_step_photos_kept} step pictures are kept.
        </p>
      </div>
      <div>
        <h3>Smoothing</h3>
        {smoothing}
      </div>
    </section>
  )
}
