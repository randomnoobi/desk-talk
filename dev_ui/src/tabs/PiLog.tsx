// The Pi log tab: one of the Pi's logs, live, like `docker compose logs -f` over ssh in a terminal.
// The server runs ssh to the Pi while this tab asks, every settings.poll_sec, and stops it by itself a
// while after the asking stops, so the tab only asks while it is shown. Each ask gets the lines after
// the last one this page has. The page keeps the last settings.pi_log.keep_lines of them.
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { getPiLog, type PiLog as PiLogReply, type PiLogLine, type PiLogSource, type PiLogState } from '../api'
import { PI_LOG_SOURCE_KEY, readChoice, saveChoice } from '../browserStorage'
import { formatClock } from '../format'
import { ErrorLine, Pill, type Level, type TabProps } from '../ui'

const wordsOf = (e: unknown) => (e instanceof Error ? e.message : String(e))

const S: Record<string, CSSProperties> = {
  grow: { flex: 1 },
  problem: { margin: '10px 0 0' },
  box: { marginTop: 12 },
}

const LEVELS: Record<PiLogState, Level> = { connected: 'ok', connecting: 'warn', waiting: 'bad', stopped: 'off' }

/** How the connection is doing, in words. `now` is seconds since 1970. */
function stateWords(reply: PiLogReply | null, now: number): string {
  if (reply === null || reply.state === 'connecting') return 'Connecting…'
  if (reply.state === 'connected') return 'Connected'
  if (reply.state === 'stopped') return 'Stopped — it starts again while this tab is open'
  const left = reply.retry_at === null ? 0 : Math.ceil(reply.retry_at - now)
  return left > 0 ? `Connection lost — trying again in ${left} s` : 'Connection lost — trying again now'
}

/** The remembered source if it's still in the list, otherwise the first one ('' when there are none). */
function firstSource(sources: PiLogSource[]): string {
  const saved = readChoice(PI_LOG_SOURCE_KEY)
  if (saved !== null && sources.some(s => s.name === saved)) return saved
  return sources[0]?.name ?? ''
}

const lineClass = (line: PiLogLine) =>
  line.kind === 'note' ? 'muted' : line.kind === 'problem' ? 'error' : undefined

export default function PiLog(props: TabProps): JSX.Element {
  const { settings, shown } = props
  const { sources, keep_lines: keep } = settings.pi_log
  const everyMs = settings.poll_sec * 1000

  const [source, setSource] = useState(() => firstSource(sources))
  const [lines, setLines] = useState<PiLogLine[]>([])
  const [reply, setReply] = useState<PiLogReply | null>(null)
  const [dropped, setDropped] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [words, setWords] = useState('')
  // The last line number this page has: the next ask is for the lines after it. Kept while the tab is
  // hidden, so coming back carries on where it was.
  const after = useRef(0)

  // Ask while the tab is shown. Hiding it stops the asking, and the server then stops ssh by itself.
  useEffect(() => {
    if (!shown || !source) return
    let gone = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const ask = async () => {
      try {
        const now = await getPiLog(source, after.current)
        if (gone) return
        if (now.end < after.current) {
          after.current = 0           // the server started its numbers over (it restarted): ask for all it has
        } else {
          after.current = now.end
          if (now.lines.length) setLines(old => [...old, ...now.lines].slice(-keep))
          if (now.dropped) setDropped(true)
        }
        setReply(now)
        setError(null)
      } catch (e) {
        if (gone) return
        setError(wordsOf(e))
      }
      if (!gone) timer = setTimeout(ask, everyMs)
    }
    void ask()
    return () => {
      gone = true
      clearTimeout(timer)
    }
  }, [shown, source, everyMs, keep])

  const pick = (name: string) => {
    saveChoice(PI_LOG_SOURCE_KEY, name)
    after.current = 0
    setLines([])
    setDropped(false)
    setReply(null)
    setError(null)
    setSource(name)
  }

  const clear = () => {
    setLines([])
    setDropped(false)
  }

  const needle = words.toLowerCase()
  const visible = needle ? lines.filter(l => l.text.toLowerCase().includes(needle)) : lines

  // Keep the box scrolled to the end, unless you've scrolled up to read something.
  const box = useRef<HTMLDivElement>(null)
  const atEnd = useRef(true)
  useEffect(() => {
    const el = box.current
    if (el && atEnd.current) el.scrollTop = el.scrollHeight
  }, [visible, shown])

  if (sources.length === 0) {
    return <div className="card"><p className="muted">The settings list no Pi logs to read.</p></div>
  }

  return (
    <div className="card">
      <div className="row">
        <label htmlFor="pi-log-source">Log</label>
        <select id="pi-log-source" className="input" value={source} onChange={e => pick(e.target.value)}>
          {sources.map(s => <option key={s.name} value={s.name}>{s.label}</option>)}
        </select>
        <span role="status">
          <Pill level={reply ? LEVELS[reply.state] : 'warn'}>{stateWords(reply, Date.now() / 1000)}</Pill>
        </span>
        <span style={S.grow} />
        <input type="search" className="input" aria-label="Only lines with" placeholder="Only lines with…"
               value={words} onChange={e => setWords(e.target.value)} />
        <button type="button" className="btn" onClick={clear}>Clear</button>
      </div>
      {reply?.problem && <p className="error mono" style={S.problem}>{reply.problem}</p>}
      <ErrorLine error={error} />
      {dropped && <p className="muted small" style={S.problem}>Some older lines were let go before this page read them.</p>}

      <div ref={box} className="printout" role="log" aria-label="Pi log" style={S.box}
           onScroll={e => {
             const el = e.currentTarget
             atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < el.clientHeight / 4
           }}>
        {visible.map(l => (
          <div key={l.number} data-number={l.number} className={lineClass(l)} title={formatClock(l.at)}>{l.text}</div>
        ))}
        {visible.length === 0 && (
          <div className="muted">{lines.length > 0 ? 'No line has that.' : 'Waiting for the first lines…'}</div>
        )}
      </div>
    </div>
  )
}
