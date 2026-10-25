// The System tab's rows: how the Pi and every part of the app are doing, each with a level
// (ok, warn, bad, or off when not in use). The limits come from settings → dev.health.
import type { Health, HealthLimits, LogStatus, PiStatus } from './api'
import { formatAgo, formatBytes, formatSeconds } from './format'
import type { Level } from './ui'

export interface Row {
  group: string
  name: string
  value: string
  level: Level
}

/** What one poll saw, kept so the next poll can work out "per second" and when the last picture came. */
export interface Seen {
  at: number                         // seconds since 1970
  pictures: number | null            // camera.pictures_received
  frameId: string                    // the Pi's newest picture id ('' when not the Pi)
  handled: Record<string, number>    // per event consumer
  lastPictureAt: number | null       // when this page first saw the newest picture; null = not seen one come in
}

/** What this poll saw, given what the one before saw (null for the first). */
export function remember(before: Seen | null, health: Health, at: number): Seen {
  const pictures = health.camera.pictures_received
  const frameId = health.pi?.last_frame_id ?? ''
  const newPicture = before !== null && (pictures !== before.pictures || frameId !== before.frameId)
  return {
    at,
    pictures,
    frameId,
    handled: Object.fromEntries(health.events.map(c => [c.name, c.handled])),
    lastPictureAt: newPicture ? at : before?.lastPictureAt ?? null,
  }
}

const MBYTE = 1024 * 1024

const PI_ROWS = ['connected', 'pictures per second', 'last picture', 'detector', 'detect ms', 'model', 'CPU °C',
                 'slowed down', 'disk free', 'photos stored', 'last cleanup', 'clock off by'] as const

/** How much something went up a second between the poll before and now; null without one before. */
function perSecond(now: number, then: number | null | undefined, before: Seen | null, nowAt: number): number | null {
  if (before === null || then === null || then === undefined || nowAt <= before.at) return null
  return Math.max(0, now - then) / (nowAt - before.at)
}

const rate = (n: number) => String(Number(n.toFixed(1)))
const ms = (n: number) => `${Math.round(n)} ms`
const notSent: [string, Level] = ['not sent yet', 'off']

function piRows(health: Health, pi: PiStatus | null, limits: HealthLimits, now: number,
                before: Seen | null): Row[] {
  const group = 'Pi'
  if (pi === null) {
    const value = `off: the camera is ${health.camera.source}`
    return PI_ROWS.map(name => ({ group, name, value, level: 'off' }))
  }
  const { connected, pictures_received } = health.camera
  const seen = remember(before, health, now)
  const cells: Record<(typeof PI_ROWS)[number], [string, Level]> = {
    connected: connected ? ['yes', 'ok'] : ['no', 'bad'],
    'pictures per second': (() => {
      const r = pictures_received === null ? health.camera.fps
        : perSecond(pictures_received, before?.pictures, before, now)
      if (r === null) return ['working it out…', 'off']
      return [rate(r), r > 0 ? 'ok' : 'warn']
    })(),
    // How old the newest picture is, as this page saw it come in. The red comes from the server's own
    // "connected" answer, which uses settings → camera.disconnect_after_sec.
    'last picture': [
      seen.lastPictureAt !== null ? formatAgo(seen.lastPictureAt, now) : connected ? 'recently' : 'none lately',
      connected ? 'ok' : 'bad',
    ],
    detector: pi.detector === null ? ['no picture yet', 'off']
      : pi.detector === 'off' ? [pi.detector_reason ? `off: ${pi.detector_reason}` : 'off', 'off']
      : [pi.detector, pi.detector === 'busy' ? 'warn' : 'ok'],
    'detect ms': pi.detect_ms === null ? ['none yet', 'off'] : [ms(pi.detect_ms), 'ok'],
    model: pi.model ? [pi.model, 'ok'] : ['none', 'off'],
    'CPU °C': pi.cpu_temp_c === undefined ? notSent
      : [String(Number(pi.cpu_temp_c.toFixed(1))),
         pi.cpu_temp_c >= limits.pi_cpu_bad_c ? 'bad' : pi.cpu_temp_c >= limits.pi_cpu_warn_c ? 'warn' : 'ok'],
    // The Pi's slow-down flags: anything but 0x0 means it was too hot or short of power.
    'slowed down': pi.throttled === undefined ? notSent
      : parseInt(pi.throttled, 16) === 0 ? ['no', 'ok'] : [`yes (${pi.throttled})`, 'bad'],
    'disk free': pi.disk_free_mb === undefined ? notSent
      : [formatBytes(pi.disk_free_mb * MBYTE), pi.disk_free_mb < limits.pi_disk_warn_mb ? 'warn' : 'ok'],
    'photos stored': pi.photos_stored === undefined ? notSent : [String(pi.photos_stored), 'ok'],
    'last cleanup': pi.last_cleanup_ts === undefined ? notSent : [formatAgo(pi.last_cleanup_ts, now), 'ok'],
    'clock off by': pi.clock_off_sec === null ? ['not known yet', 'off']
      : [pi.clock_off_sec === 0 ? '0 s'
          : `${formatSeconds(pi.clock_off_sec)} ${pi.clock_off_sec < 0 ? 'behind' : 'ahead'}`, 'ok'],
  }
  return PI_ROWS.map(name => ({ group, name, value: cells[name][0], level: cells[name][1] }))
}

function detectionRows(health: Health, limits: HealthLimits): Row[] {
  const group = 'Detection on this computer'
  const d = health.detection
  return [
    { group, name: 'pictures analysed', value: String(d.pictures_analysed), level: 'ok' },
    d.detect_ms === null
      ? { group, name: 'detect ms', value: 'none yet', level: 'off' }
      : { group, name: 'detect ms', value: ms(d.detect_ms), level: d.detect_ms > limits.detect_warn_ms ? 'warn' : 'ok' },
    { group, name: 'model', value: d.model || 'none', level: d.model ? 'ok' : 'off' },
    { group, name: 'problem', value: d.problem ?? 'none', level: d.problem ? 'bad' : 'ok' },
  ]
}

function streamRows(health: Health, now: number, before: Seen | null): Row[] {
  return health.events.map(c => {
    const r = perSecond(c.handled, before?.handled[c.name], before, now)
    const handled = r === null ? `handled ${c.handled}` : `handled ${c.handled} (${rate(r)} a second)`
    return {
      group: 'Event stream', name: c.name,
      value: `${handled}, waiting ${c.waiting}, dropped ${c.dropped}, errors ${c.errors}`,
      level: c.errors > 0 ? 'bad' : c.dropped > 0 ? 'warn' : 'ok',
    }
  })
}

function jobRows(health: Health): Row[] {
  return health.jobs.map(j => {
    const parts = [j.state, `${j.total_failures} failures`]
    if (j.last_error) parts.push(`last error: ${j.last_error}`)
    const level: Level = j.state === 'stopped' ? 'bad' : j.state === 'waiting after error' ? 'warn' : 'ok'
    return { group: 'Background jobs', name: j.name, value: parts.join(', '), level }
  })
}

function timedRows(health: Health): Row[] {
  return health.scheduler.map(t => {
    const parts = [`${t.runs} runs`, `next in ${formatSeconds(t.next_in_sec)}`]
    if (t.last_error) parts.push(`last error: ${t.last_error}`)
    return { group: 'Timed jobs', name: t.name, value: parts.join(', '), level: t.last_error ? 'warn' : 'ok' }
  })
}

function logRows(log: LogStatus, now: number): Row[] {
  const group = 'Event log'
  const counts = Object.entries(log.today.events).map(([kind, n]) => `${kind} ${n}`).join(', ')
  return [
    { group, name: 'today', value: counts || 'nothing yet', level: 'ok' },
    { group, name: 'pictures waiting', value: String(log.today.pictures_waiting), level: 'ok' },
    { group, name: 'last compaction',
      value: log.last_compaction ? formatAgo(log.last_compaction.ts, now) : 'not yet', level: 'ok' },
    { group, name: 'folder', value: log.folder, level: 'ok' },
  ]
}

function aiRows(health: Health): Row[] {
  const group = 'AI'
  const ai = health.ai
  return [
    { group, name: 'ready', value: ai.ready ? 'yes' : 'no', level: ai.ready ? 'ok' : 'bad' },
    { group, name: 'which one', value: ai.label, level: 'ok' },
    { group, name: 'problem', value: ai.problem ?? 'none', level: ai.problem ? 'bad' : 'ok' },
  ]
}

/**
 * Every row of the System tab, in order. `now` is seconds since 1970. `before` is what the poll before
 * saw (from remember()); without it, the "per second" numbers wait for the next poll.
 */
export function healthRows(health: Health, logStatus: LogStatus, limits: HealthLimits, now: number,
                           before: Seen | null = null): Row[] {
  return [
    ...piRows(health, health.pi, limits, now, before),
    ...detectionRows(health, limits),
    ...streamRows(health, now, before),
    ...jobRows(health),
    ...timedRows(health),
    ...logRows(logStatus, now),
    ...aiRows(health),
  ]
}

/** The group names in the order the tab shows them. */
export const GROUPS = ['Pi', 'Detection on this computer', 'Event stream', 'Background jobs', 'Timed jobs',
                       'Event log', 'AI'] as const
