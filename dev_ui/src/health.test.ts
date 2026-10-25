import { describe, expect, it } from 'vitest'
import type { Health, LogStatus, PiStatus } from './api'
import { healthRows, remember, type Row, type RowLimits } from './health'

const limits: RowLimits = {
  pi_cpu_warn_c: 70, pi_cpu_bad_c: 80, pi_disk_warn_mb: 1000, detect_warn_ms: 500, disconnect_after_sec: 3,
}
const NOW = 1_800_000_000

function pi(over: Partial<PiStatus> = {}): PiStatus {
  return {
    last_frame_id: 'f-12', camera: 'picamera2', photos_stored: 40, disk_free_mb: 5000, last_cleanup_ts: NOW - 120,
    cpu_temp_c: 55, throttled: '0x0', upload_format: 2, detector: 'ok', detector_reason: '', detect_ms: 180,
    model: 'yolo11n', clock_off_sec: -0.4, ...over,
  }
}

function health(over: Partial<Health> = {}): Health {
  return {
    uptime_sec: 600,
    jobs: [{ name: 'camera', state: 'running', failures_in_row: 0, total_failures: 0, last_error: null, last_ok_at: NOW }],
    scheduler: [{ name: 'compact log', every_sec: 3600, runs: 2, last_run_at: null, last_error: null,
                  next_run_at: NOW + 30, next_in_sec: 30 }],
    events: [{ name: 'habits', handled: 100, waiting: 0, dropped: 0, errors: 0 }],
    camera: { source: 'pi', connected: true, fps: 3, pictures_received: 300 },
    pi: pi(),
    detection: { pictures_analysed: 300, detect_ms: 120, problem: null, model: 'yolo11n' },
    ai: { ready: true, label: 'Claude', problem: null },
    ...over,
  }
}

const log: LogStatus = {
  folder: '/data/log',
  today: { pictures_waiting: 4, events: { alert: 2, object_state: 40 } },
  last_compaction: null,
  scheduler: [],
  stream: [],
  camera: { source: 'pi', connected: true, fps: 3, frames_received: 300, yolo_ms: 120, model: 'yolo11n' },
}

function row(rows: Row[], group: string, name: string): Row {
  const found = rows.find(r => r.group === group && r.name === name)
  if (!found) throw new Error(`no row ${group} / ${name} in ${JSON.stringify(rows.map(r => [r.group, r.name]))}`)
  return found
}

describe('healthRows', () => {
  it('turns a hot Pi red, and a warm one amber', () => {
    expect(row(healthRows(health({ pi: pi({ cpu_temp_c: 82 }) }), log, limits, NOW), 'Pi', 'CPU °C').level).toBe('bad')
    expect(row(healthRows(health({ pi: pi({ cpu_temp_c: 72 }) }), log, limits, NOW), 'Pi', 'CPU °C').level).toBe('warn')
    expect(row(healthRows(health(), log, limits, NOW), 'Pi', 'CPU °C').level).toBe('ok')
  })

  it('says the Pi slowed itself down when throttled is anything but 0x0', () => {
    const slowed = row(healthRows(health({ pi: pi({ throttled: '0x50005' }) }), log, limits, NOW), 'Pi', 'slowed down')
    expect(slowed.level).toBe('bad')
    expect(slowed.value).toContain('0x50005')
    expect(row(healthRows(health(), log, limits, NOW), 'Pi', 'slowed down').level).toBe('ok')
  })

  it('turns a consumer that dropped events amber, and one with errors red', () => {
    const dropped = health({ events: [{ name: 'habits', handled: 100, waiting: 0, dropped: 3, errors: 0 }] })
    expect(row(healthRows(dropped, log, limits, NOW), 'Event stream', 'habits').level).toBe('warn')
    const errors = health({ events: [{ name: 'habits', handled: 100, waiting: 0, dropped: 3, errors: 1 }] })
    expect(row(healthRows(errors, log, limits, NOW), 'Event stream', 'habits').level).toBe('bad')
  })

  it('greys out every Pi row when the camera is not the Pi', () => {
    const rows = healthRows(health({ pi: null, camera: { source: 'webcam', connected: true, fps: 3, pictures_received: 9 } }),
                            log, limits, NOW)
    const piRows = rows.filter(r => r.group === 'Pi')
    expect(piRows.length).toBeGreaterThan(5)
    for (const r of piRows) {
      expect(r.level).toBe('off')
      expect(r.value).toBe('off: the camera is webcam')
    }
  })

  it('turns little free space on the Pi amber', () => {
    expect(row(healthRows(health({ pi: pi({ disk_free_mb: 500 }) }), log, limits, NOW), 'Pi', 'disk free').level).toBe('warn')
  })

  it('turns slow detection on this computer amber', () => {
    const slow = health({ detection: { pictures_analysed: 3, detect_ms: 700, problem: null, model: 'yolo11n' } })
    expect(row(healthRows(slow, log, limits, NOW), 'Detection on this computer', 'detect ms').level).toBe('warn')
  })

  it('shows the Pi detector off with its reason, and a missing camera as red', () => {
    const off = health({ pi: pi({ detector: 'off', detector_reason: 'no model file' }),
                         camera: { source: 'pi', connected: false, fps: 0, pictures_received: 300 } })
    const rows = healthRows(off, log, limits, NOW)
    expect(row(rows, 'Pi', 'detector').value).toBe('off: no model file')
    expect(row(rows, 'Pi', 'connected').level).toBe('bad')
    expect(row(rows, 'Pi', 'last picture').level).toBe('bad')
  })

  it('works out pictures and events per second from the poll before', () => {
    const first = health()
    const before = remember(null, first, NOW)
    const later = health({ camera: { source: 'pi', connected: true, fps: 3, pictures_received: 330 },
                           events: [{ name: 'habits', handled: 150, waiting: 0, dropped: 0, errors: 0 }] })
    const rows = healthRows(later, log, limits, NOW + 10, before)
    expect(row(rows, 'Pi', 'pictures per second').value).toBe('3')
    expect(row(rows, 'Event stream', 'habits').value).toContain('5 a second')
    expect(row(rows, 'Pi', 'last picture').value).toBe('0 s ago')
  })

  it('remembers when the last new picture came in', () => {
    const before = remember(null, health(), NOW)
    const seen = remember(before, health({ camera: { source: 'pi', connected: true, fps: 3, pictures_received: 301 } }), NOW + 5)
    expect(seen.lastPictureAt).toBe(NOW + 5)
    const still = remember(seen, health({ camera: { source: 'pi', connected: true, fps: 3, pictures_received: 301 } }), NOW + 9)
    expect(still.lastPictureAt).toBe(NOW + 5)
    expect(row(healthRows(health({ camera: { source: 'pi', connected: true, fps: 3, pictures_received: 301 } }),
                          log, limits, NOW + 9, seen), 'Pi', 'last picture').value).toBe('4 s ago')
  })

  it('turns the last picture red once it is older than settings → camera.disconnect_after_sec', () => {
    const before = remember(null, health(), NOW)
    const newer = health({ camera: { source: 'pi', connected: true, fps: 3, pictures_received: 301 } })
    const seen = remember(before, newer, NOW + 5)
    expect(row(healthRows(newer, log, limits, NOW + 7, seen), 'Pi', 'last picture').level).toBe('ok')
    expect(row(healthRows(newer, log, limits, NOW + 9, seen), 'Pi', 'last picture').level).toBe('bad')
    expect(row(healthRows(newer, log, { ...limits, disconnect_after_sec: 10 }, NOW + 9, seen), 'Pi', 'last picture').level)
      .toBe('ok')
  })

  it('lists jobs, timed jobs, the event log and the AI', () => {
    const rows = healthRows(health({
      jobs: [{ name: 'camera', state: 'waiting after error', failures_in_row: 2, total_failures: 5,
               last_error: 'timed out', last_ok_at: null }],
      ai: { ready: false, label: 'Claude', problem: 'no API key' },
    }), log, limits, NOW)
    expect(row(rows, 'Background jobs', 'camera').level).toBe('warn')
    expect(row(rows, 'Background jobs', 'camera').value).toContain('timed out')
    expect(row(rows, 'Timed jobs', 'compact log').value).toContain('next in 30 s')
    expect(row(rows, 'Event log', 'folder').value).toBe('/data/log')
    expect(row(rows, 'Event log', 'today').value).toContain('alert 2')
    expect(row(rows, 'AI', 'ready').level).toBe('bad')
    expect(row(rows, 'AI', 'problem').value).toBe('no API key')
  })
})
