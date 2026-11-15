import { useRef } from 'react'
import { getShownHealth, getShownLogStatus, type DevSettings, type Whose } from '../api'
import { formatClock, formatSeconds } from '../format'
import { GROUPS, healthRows, remember, type Row, type Seen } from '../health'
import { ErrorLine, Table } from '../ui'
import { usePoll } from '../usePoll'

const COLUMNS = [
  { label: 'What', cell: (r: Row) => r.name },
  { label: 'Now', cell: (r: Row) => r.value },
]

/** Whose Desk Talk the heading is about: the Pi's own (when the Pi is the camera and this page runs on a computer)
 *  or this computer's. */
function whoseWords(whose: Whose): string {
  return whose.from === 'pi' ? `The Pi's own Desk Talk (${whose.address})` : 'Desk Talk'
}

/** The System tab: how the Pi and every part of the app are doing, asked again every settings.poll_sec. When the Pi
 *  is the camera, the server passes on the Pi's own Desk Talk's replies, since this computer's gets no pictures. */
export default function System(props: { settings: DevSettings }): JSX.Element {
  const { settings } = props
  // What the poll before saw, so pictures and events per second come from the change between two polls.
  const seen = useRef<Seen | null>(null)
  const poll = usePoll(async () => {
    const [health, log] = await Promise.all([getShownHealth(), getShownLogStatus()])
    const at = Date.now() / 1000
    const before = seen.current
    seen.current = remember(before, health, at)
    const limits = { ...settings.health, disconnect_after_sec: settings.disconnect_after_sec }
    return {
      rows: healthRows(health, log, limits, at, before), uptime: health.uptime_sec, at,
      whose: { from: health.from, address: health.address },
    }
  }, settings.poll_sec * 1000)

  const reply = poll.data
  return (
    <div className="stack">
      <div className="card">
        <h2>System</h2>
        <p className="muted small">
          {reply
            ? `${whoseWords(reply.whose)} has been running for ${formatSeconds(reply.uptime)}. Last asked at ${formatClock(reply.at)}, again every ${settings.poll_sec} s.`
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
