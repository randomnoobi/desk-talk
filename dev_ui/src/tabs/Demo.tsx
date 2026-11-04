// The Demo tab: pretend warnings (a cup at the desk edge, a drink by the laptop, a drink reminder ...) that
// fire on purpose once a countdown is over, for testing and for recording a demo video. Unlike the other
// tabs, this one changes what the app does: each pretend warning really goes out, the same way a real one
// does. The list, the default countdown and the longest one come from the server (settings → dev.scenarios).
import { useEffect, useState } from 'react'
import { cancelScenario, getScenarios, startScenario, type DevSettings, type WaitingScenario } from '../api'
import { ErrorLine } from '../ui'
import { usePoll } from '../usePoll'

// The countdowns are shown in whole seconds, so the page redraws them once a second between the server's
// answers. (A unit, not a setting: how often to ask the server is settings.poll_sec.)
const ONE_SECOND_MS = 1000

/** Whole seconds left, `msSinceAnswer` after the server said `secondsLeft`. Never below 0. */
export function secondsLeftNow(secondsLeft: number, msSinceAnswer: number): number {
  return Math.max(0, Math.ceil(secondsLeft - msSinceAnswer / 1000))
}

export default function Demo(props: { settings: DevSettings }): JSX.Element {
  const { settings } = props
  const limits = settings.scenarios
  const poll = usePoll(getScenarios, settings.poll_sec * 1000)
  const scenarios = poll.data?.scenarios ?? []
  const waiting = poll.data?.waiting ?? []

  const [picked, setPicked] = useState<string>('')
  const [seconds, setSeconds] = useState<string>(String(limits.countdown_default_sec))
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!picked && scenarios.length > 0) setPicked(scenarios[0].name)
  }, [picked, scenarios])

  const act = async (what: () => Promise<unknown>) => {
    setSending(true)
    setError(null)
    try {
      await what()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSending(false)
      poll.refresh()
    }
  }

  return (
    <div className="stack">
      <div className="card stack">
        <h2>Pretend warnings</h2>
        <p className="muted">
          These go out like real ones once the countdown is over: in the chat, as Mac pop-ups and in the event
          log, through the same parts as the real warnings. A pretend sip counts in today's water. Use them to
          test, or to record a demo video.
        </p>
        <div className="row">
          <label htmlFor="demo-pick">Pretend warning</label>
          <select id="demo-pick" value={picked} onChange={e => setPicked(e.target.value)}>
            {scenarios.map(s => <option key={s.name} value={s.name}>{s.label}</option>)}
          </select>
          <label htmlFor="demo-seconds">Seconds</label>
          <input id="demo-seconds" type="number" min={0} max={limits.countdown_max_sec} step={1} value={seconds}
                 onChange={e => setSeconds(e.target.value)} style={{ width: '6em' }} />
          <button type="button" className="btn primary" disabled={sending || !picked || seconds === ''}
                  onClick={() => void act(() => startScenario(picked, Number(seconds)))}>
            Start
          </button>
        </div>
        <p className="muted small">
          From 0 to {limits.countdown_max_sec} seconds; at most {limits.waiting_max} counting down at once.
        </p>
        <ErrorLine error={error ?? poll.error} />
      </div>

      <div className="card stack">
        <h2>Counting down</h2>
        {waiting.length === 0
          ? <p className="muted">Nothing is counting down.</p>
          : <Countdowns waiting={waiting} answeredAt={poll.lastOk ?? Date.now()} disabled={sending}
                        onCancel={id => void act(() => cancelScenario(id))} />}
      </div>
    </div>
  )
}

function Countdowns(props: {
  waiting: WaitingScenario[]
  answeredAt: number
  disabled: boolean
  onCancel: (id: string) => void
}): JSX.Element {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ONE_SECOND_MS)
    return () => clearInterval(timer)
  }, [])
  const since = Math.max(0, now - props.answeredAt)
  return (
    <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
      {props.waiting.map(w => {
        const left = secondsLeftNow(w.seconds_left, since)
        return (
          <li key={w.id} className="row">
            <span className="waiting-label">{w.label}</span>
            <strong className="mono" aria-live="polite">{left > 0 ? `${left} s` : 'going off…'}</strong>
            <button type="button" className="btn" disabled={props.disabled} onClick={() => props.onCancel(w.id)}>
              Cancel
            </button>
          </li>
        )
      })}
    </ul>
  )
}
