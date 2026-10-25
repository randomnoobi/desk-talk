import { useRef } from 'react'
import { getHealth, getLogStatus, type DevSettings } from '../api'
import { formatClock, formatSeconds } from '../format'
import { GROUPS, healthRows, remember, type Row, type Seen } from '../health'
import { ErrorLine, Table } from '../ui'
import { usePoll } from '../usePoll'

const COLUMNS = [
  { label: 'What', cell: (r: Row) => r.name },
  { label: 'Now', cell: (r: Row) => r.value },
]

/** The System tab: how the Pi and every part of the app are doing, asked again every settings.poll_sec. */
export default function System(props: { settings: DevSettings }): JSX.Element {
  const { settings } = props
  // What the poll before saw, so pictures and events per second come from the change between two polls.
  const seen = useRef<Seen | null>(null)
  const poll = usePoll(async () => {
    const [health, log] = await Promise.all([getHealth(), getLogStatus()])
    const at = Date.now() / 1000
    const before = seen.current
    seen.current = remember(before, health, at)
    const limits = { ...settings.health, disconnect_after_sec: settings.disconnect_after_sec }
    return { rows: healthRows(health, log, limits, at, before), uptime: health.uptime_sec, at }
  }, settings.poll_sec * 1000)

  const reply = poll.data
  return (
    <div className="stack">
      <div className="card">
        <h2>System</h2>
        <p className="muted small">
          {reply
            ? `Desk Talk has been running for ${formatSeconds(reply.uptime)}. Last asked at ${formatClock(reply.at)}, again every ${settings.poll_sec} s.`
            : 'Asking Desk Talk how it is doing…'}
        </p>
        <ErrorLine error={poll.error} />
      </div>
      {reply && GROUPS.map(group => {
        const rows = reply.rows.filter(r => r.group === group)
        return (
          <div className="card" key={group}>
            <h3>{group}</h3>
            <Table columns={COLUMNS} rows={rows} rowKey={r => r.name} level={r => r.level} empty="Nothing here." />
          </div>
        )
      })}
    </div>
  )
}
