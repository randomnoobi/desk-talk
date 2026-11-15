import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError, getHealth, getLogStatus, getShownHealth, NO_ANSWER, type DevSettings, type Health, type LogStatus,
  type ShownHealth,
} from '../api'
import System from './System'

vi.mock('../api', async original => ({
  ...await original<typeof import('../api')>(),
  getShownHealth: vi.fn(),
  getHealth: vi.fn(),
  getLogStatus: vi.fn(),
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

// This computer's own Desk Talk (GET /api/health).
const health: Health = {
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

// GET /api/dev/health when it is this computer's own.
const shownHere: ShownHealth = { ...health, from: 'here', address: '' }

// GET /api/dev/health when it is the Pi's own Desk Talk: its camera check is red, and it has a job of its own.
const shownPi: ShownHealth = {
  ...health, from: 'pi', address: 'desktalk-pi.local:5000', uptime_sec: 39_600,
  jobs: [{ name: 'pi only job', state: 'running', failures_in_row: 0, total_failures: 0, last_error: null, last_ok_at: null }],
  checks: { ...health.checks, camera: { level: 'serious', value: null, unit: '' } },
}

// This computer's own, with a job of its own, when the Pi is the camera.
const ownWithJob: Health = {
  ...health,
  jobs: [{ name: 'cloud sender', state: 'running', failures_in_row: 0, total_failures: 0, last_error: null, last_ok_at: null }],
}

const log: LogStatus = {
  folder: '/data/log', today: { pictures_waiting: 0, events: {} }, last_compaction: null, scheduler: [], stream: [],
  camera: { source: 'pi', connected: true, fps: 3, frames_received: 10, yolo_ms: 90, model: 'yolo11n' },
}

const THIS_COMPUTER = ['Detection in the app', 'Event stream', 'Background jobs', 'Timed jobs', 'Event log', 'AI']

afterEach(() => vi.resetAllMocks())

describe('System tab, when the page shows this computer\'s own Desk Talk', () => {
  it('shows each group with its rows, coloured by level', async () => {
    vi.mocked(getShownHealth).mockResolvedValue(shownHere)
    vi.mocked(getHealth).mockResolvedValue(health)
    vi.mocked(getLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByRole('heading', { name: 'Pi' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Event stream' })).toBeInTheDocument()
    expect(screen.getByText('CPU °C').closest('tr')).toHaveClass('bad')
    expect(screen.getByRole('heading', { name: 'Health checks' })).toBeInTheDocument()
    expect(screen.getByText('free space where history is saved').closest('tr')).toHaveClass('warn')
    expect(screen.getByText('free space on the rest of the card').closest('tr')).toHaveClass('off')
    expect(screen.getByText('saving history').closest('tr')).toHaveClass('ok')
    expect(screen.getByText('/data/log')).toBeInTheDocument()
  })

  it('says Desk Talk has been running, with no "this computer" labels', async () => {
    vi.mocked(getShownHealth).mockResolvedValue(shownHere)
    vi.mocked(getHealth).mockResolvedValue(health)
    vi.mocked(getLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByText(/^Desk Talk has been running for 2 min 5 s\. Last asked at .+, again every 2 s\.$/))
      .toBeInTheDocument()
    for (const group of THIS_COMPUTER) {
      expect(await screen.findByRole('heading', { name: group })).toBeInTheDocument()
    }
    expect(screen.queryByText(/this computer/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/the Pi's own Desk Talk/i)).not.toBeInTheDocument()
  })

  it('says what went wrong, once, when the server does not answer', async () => {
    vi.mocked(getShownHealth).mockRejectedValue(new ApiError(0, NO_ANSWER))
    vi.mocked(getHealth).mockRejectedValue(new ApiError(0, NO_ANSWER))
    vi.mocked(getLogStatus).mockRejectedValue(new ApiError(0, NO_ANSWER))
    render(<System settings={settings} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(NO_ANSWER)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
  })
})

describe('System tab, when the Pi is the camera and the page runs on a computer', () => {
  it('takes the Health checks and the Pi from the Pi\'s own Desk Talk, and the rest from this computer, marked so', async () => {
    vi.mocked(getShownHealth).mockResolvedValue(shownPi)
    vi.mocked(getHealth).mockResolvedValue(ownWithJob)
    vi.mocked(getLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByText(
      /^The Pi's own Desk Talk \(desktalk-pi\.local:5000\) has been running for 11 h\. Last asked at .+, again every 2 s\.$/,
    )).toBeInTheDocument()
    expect(await screen.findByText(/cards marked "this computer" are this computer's own copy of Desk Talk/))
      .toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Health checks' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Pi' })).toBeInTheDocument()
    expect(screen.getByText('camera pictures').closest('tr')).toHaveClass('bad')
    for (const group of THIS_COMPUTER) {
      expect(screen.getByRole('heading', { name: `${group} (this computer)` })).toBeInTheDocument()
    }
    expect(screen.getByText('cloud sender')).toBeInTheDocument()
    expect(screen.queryByText('pi only job')).not.toBeInTheDocument()
  })

  it('shows the words of a 502 where the Pi\'s cards go, and still this computer\'s own cards', async () => {
    const words = "Couldn't reach the Pi's Desk Talk at desktalk-pi.local:5000."
    vi.mocked(getShownHealth).mockRejectedValue(new ApiError(502, words))
    vi.mocked(getHealth).mockResolvedValue(ownWithJob)
    vi.mocked(getLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(words)
    expect(screen.getByRole('alert').closest('.card')).toHaveTextContent(/Health checks and Pi/)
    expect(await screen.findByRole('heading', { name: 'Background jobs (this computer)' })).toBeInTheDocument()
    expect(screen.getByText('cloud sender')).toBeInTheDocument()
    expect(screen.queryByText('camera pictures')).not.toBeInTheDocument()
  })

  it('still shows the Pi\'s checks when this computer\'s own Desk Talk does not answer', async () => {
    vi.mocked(getShownHealth).mockResolvedValue(shownPi)
    vi.mocked(getHealth).mockRejectedValue(new ApiError(500, 'Server error 500'))
    vi.mocked(getLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Server error 500')
    expect(await screen.findByText('camera pictures')).toBeInTheDocument()
    expect(screen.getByText('camera pictures').closest('tr')).toHaveClass('bad')
    expect(screen.queryByRole('heading', { name: 'Background jobs (this computer)' })).not.toBeInTheDocument()
  })
})
