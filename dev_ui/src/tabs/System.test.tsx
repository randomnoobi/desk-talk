import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError, getShownHealth, getShownLogStatus, NO_ANSWER, type DevSettings, type ShownHealth, type ShownLogStatus,
} from '../api'
import System from './System'

vi.mock('../api', async original => ({
  ...await original<typeof import('../api')>(),
  getShownHealth: vi.fn(),
  getShownLogStatus: vi.fn(),
}))

const settings: DevSettings = {
  poll_sec: 2, checks: [], health: { pi_disk_warn_mb: 1000, detect_warn_ms: 500 }, pi_hot: { warning_c: 75, serious_c: 80 },
  trace_pictures: 30, sql_max_rows: 500, data_folder: '/data',
  disconnect_after_sec: 3, web_port: 5000, flicker_flips_per_min: 4,
  output_keep_kb: 2000, flips_window_min: 10, table_counts_every_sec: 30, trace_events_kept: 50,
  trace_photos_kept: 6,
  trace_step_photos_kept: 24,
  object_states: { on_confidence: 0.5, off_confidence: 0.3, appear_sec: 2, gone_sec: 5, window_pictures: 5, vote_share: 0.6 },
  scenarios: { countdown_default_sec: 5, countdown_max_sec: 600, waiting_max: 10 },
  pi_log: { sources: [{ name: 'detector', label: 'Detector service' }], keep_lines: 100 },
}

const health: ShownHealth = {
  from: 'here', address: '',
  uptime_sec: 125, jobs: [], scheduler: [], events: [{ name: 'habits', handled: 4, waiting: 0, dropped: 0, errors: 0 }],
  camera: { source: 'pi', connected: true, fps: 3, pictures_received: 10 },
  pi: { last_frame_id: 'f-1', cpu_temp_c: 82, upload_format: 2, detector: 'ok', detector_reason: '', detect_ms: 150,
        model: 'yolo11n', clock_off_sec: 0.2 },
  detection: { pictures_analysed: 10, detect_ms: 90, problem: null, model: 'yolo11n' },
  ai: { ready: true, label: 'Claude', problem: null },
  checks: {
    camera: { level: 'ok', value: null, unit: '' },
    detections: { level: 'ok', value: 2, unit: 's' },
    pi_temperature: { level: 'serious', value: 82, unit: '°C' },
    card: { level: 'warning', value: 1500, unit: 'MB' },
    card_rest: { level: 'unknown', value: null, unit: 'MB' },
    saving: { level: 'ok', value: null, unit: '' },
    pi_heartbeat: { level: 'ok', value: 12, unit: 's' },
  },
}

const log: ShownLogStatus = {
  from: 'here', address: '',
  folder: '/data/log', today: { pictures_waiting: 0, events: {} }, last_compaction: null, scheduler: [], stream: [],
  camera: { source: 'pi', connected: true, fps: 3, frames_received: 10, yolo_ms: 90, model: 'yolo11n' },
}

afterEach(() => vi.resetAllMocks())

describe('System tab', () => {
  it('shows each group with its rows, coloured by level', async () => {
    vi.mocked(getShownHealth).mockResolvedValue(health)
    vi.mocked(getShownLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByRole('heading', { name: 'Pi' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Event stream' })).toBeInTheDocument()
    expect(screen.getByText('CPU °C').closest('tr')).toHaveClass('bad')
    expect(screen.getByRole('heading', { name: 'Health checks' })).toBeInTheDocument()
    expect(screen.getByText('free space where history is saved').closest('tr')).toHaveClass('warn')
    expect(screen.getByText('free space on the rest of the card').closest('tr')).toHaveClass('off')
    expect(screen.getByText('saving history').closest('tr')).toHaveClass('ok')
    expect(screen.getByText('/data/log')).toBeInTheDocument()
  })

  it('says it is this computer\'s own Desk Talk when the reply is from here', async () => {
    vi.mocked(getShownHealth).mockResolvedValue(health)
    vi.mocked(getShownLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByText(/^Desk Talk has been running for 2 min 5 s\. Last asked at .+, again every 2 s\.$/))
      .toBeInTheDocument()
    expect(screen.queryByText(/the Pi's own Desk Talk/i)).not.toBeInTheDocument()
  })

  it('says it is the Pi\'s own Desk Talk, at its address, and shows the Pi\'s checks when the reply is from the Pi', async () => {
    const fromPi: ShownHealth = {
      ...health, from: 'pi', address: 'desktalk-pi.local:5000', uptime_sec: 39_600,
      checks: { ...health.checks, camera: { level: 'serious', value: null, unit: '' } },
    }
    vi.mocked(getShownHealth).mockResolvedValue(fromPi)
    vi.mocked(getShownLogStatus).mockResolvedValue({ ...log, from: 'pi', address: 'desktalk-pi.local:5000', folder: '/pi/data/log' })
    render(<System settings={settings} />)
    expect(await screen.findByText(
      /^The Pi's own Desk Talk \(desktalk-pi\.local:5000\) has been running for 11 h\. Last asked at .+, again every 2 s\.$/,
    )).toBeInTheDocument()
    expect(screen.getByText('camera pictures').closest('tr')).toHaveClass('bad')
    expect(screen.getByText('/pi/data/log')).toBeInTheDocument()
  })

  it('shows the words of a 502 when the Pi does not answer', async () => {
    const words = "Couldn't reach the Pi's Desk Talk at desktalk-pi.local:5000."
    vi.mocked(getShownHealth).mockRejectedValue(new ApiError(502, words))
    vi.mocked(getShownLogStatus).mockRejectedValue(new ApiError(502, words))
    render(<System settings={settings} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(words)
  })

  it('says what went wrong when the server does not answer', async () => {
    vi.mocked(getShownHealth).mockRejectedValue(new ApiError(0, NO_ANSWER))
    vi.mocked(getShownLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(NO_ANSWER)
  })
})
