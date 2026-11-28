import { useRef } from 'react'
import { getHealth, getLogStatus, getShownHealth, type DevSettings } from '../api'
import { formatClock, formatSeconds, whoseWords } from '../format'
import { OWN_GROUPS, ownRows, remember, SHOWN_GROUPS, shownRows, type Row, type Seen } from '../health'
import { ErrorLine, Table } from '../ui'
import { usePoll } from '../usePoll'

const COLUMNS = [
  { label: 'What', cell: (r: Row) => r.name },
  { label: 'Now', cell: (r: Row) => r.value },
]

/** One card per group, with that group's rows. `error` (the latest call's, when it failed) goes in the first card. */
function GroupCards(props: { groups: readonly string[], rows: Row[], error: string | null, label: string }): JSX.Element {
  return (
    <>
      {props.groups.map((group, i) => (
        <div className="card" key={group}>
          <h3>{group}{props.label}</h3>
          {i === 0 && <ErrorLine error={props.error} />}
          <Table columns={COLUMNS} rows={props.rows.filter(r => r.group === group)} rowKey={r => r.name}
                 level={r => r.level} empty="Nothing here." />
        </div>
      ))}
    </>
  )
}

/** A card in place of a group of cards whose call failed before it ever worked. */
function FailedCard(props: { heading: string, error: string }): JSX.Element {
  return (
    <div className="card">
      <h3>{props.heading}</h3>
      <ErrorLine error={props.error} />
    </div>
  )
}

/**
 * The System tab, asked again every settings.poll_sec. The Health checks and the Pi come from GET /api/dev/health:
 * the Pi's own Desk Talk when the Pi is the camera and this page runs on a computer (this computer's gets no
 * pictures then), else this computer's. Everything else is always this computer's own (GET /api/health and
 * GET /api/log/status). The two are asked apart, so one failing never hides the other.
 */
export default function System(props: { settings: DevSettings }): JSX.Element {
  const { settings } = props
  const limits = { ...settings.health, disconnect_after_sec: settings.disconnect_after_sec }
  // What each one's poll before saw, so pictures and events a second come from the change between two polls of
  // the same Desk Talk.
  const seenShown = useRef<Seen | null>(null)
  const seenOwn = useRef<Seen | null>(null)

  const shown = usePoll(async () => {
    const health = await getShownHealth()
    const at = Date.now() / 1000
    const before = seenShown.current
    seenShown.current = remember(before, health, at)
    return {
      rows: shownRows(health, limits, at, before), uptime: health.uptime_sec, at,
      whose: { from: health.from, address: health.address },
    }
  }, settings.poll_sec * 1000)

  const own = usePoll(async () => {
    const [health, log] = await Promise.all([getHealth(), getLogStatus()])
    const at = Date.now() / 1000
    const before = seenOwn.current
    seenOwn.current = remember(before, health, at)
    return { rows: ownRows(health, log, limits, at, before), uptime: health.uptime_sec, at }
  }, settings.poll_sec * 1000)

  const s = shown.data
  const o = own.data
  // The rest is this computer's own copy when the Pi's own is shown, or when asking for it failed (a 502: the Pi
  // didn't answer).
  const piShown = s ? s.whose.from === 'pi' : shown.error !== null
  const label = piShown ? ' (this computer)' : ''
  // When nothing has answered and both say the same (the server itself is down), say it once.
  const sameError = !s && !o && shown.error !== null && shown.error === own.error

  let topLine = 'Asking Desk Talk how it is doing…'
  if (s) {
    topLine = `${whoseWords(s.whose)} has been running for ${formatSeconds(s.uptime)}. ` +
      `Last asked at ${formatClock(s.at)}, again every ${settings.poll_sec} s.`
  } else if (o) {
    topLine = `This computer's Desk Talk has been running for ${formatSeconds(o.uptime)}. ` +
      `Last asked at ${formatClock(o.at)}, again every ${settings.poll_sec} s.`
  }

  return (
    <div className="stack">
      <div className="card">
        <h2>System</h2>
        <p className="muted small">{topLine}</p>
        {piShown && o && (
          <p className="muted small">{'The cards marked "this computer" are this computer\'s own copy of Desk Talk.'}</p>
        )}
        {sameError && <ErrorLine error={shown.error} />}
      </div>
      {s ? <GroupCards groups={SHOWN_GROUPS} rows={s.rows} error={shown.error} label="" />
        : shown.error && !sameError ? <FailedCard heading="Health checks and Pi" error={shown.error} /> : null}
      {o ? <GroupCards groups={OWN_GROUPS} rows={o.rows} error={own.error} label={label} />
        : own.error && !sameError ? <FailedCard heading="This computer" error={own.error} /> : null}
    </div>
  )
}
