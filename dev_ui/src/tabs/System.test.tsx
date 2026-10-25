import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getHealth, getLogStatus, NO_ANSWER, type DevSettings, type Health, type LogStatus } from '../api'
import System from './System'

vi.mock('../api', async original => ({
  ...await original<typeof import('../api')>(),
  getHealth: vi.fn(),
  getLogStatus: vi.fn(),
}))

const settings: DevSettings = {
  poll_sec: 2, checks: [], health: { pi_cpu_warn_c: 70, pi_cpu_bad_c: 80, pi_disk_warn_mb: 1000, detect_warn_ms: 500 },
  trace_pictures: 30, sql_max_rows: 500, data_folder: '/data',
  object_states: { on_confidence: 0.5, off_confidence: 0.3, appear_sec: 2, gone_sec: 5, window_pictures: 5, vote_share: 0.6 },
}

const health: Health = {
  uptime_sec: 125, jobs: [], scheduler: [], events: [{ name: 'habits', handled: 4, waiting: 0, dropped: 0, errors: 0 }],
  camera: { source: 'pi', connected: true, fps: 3, pictures_received: 10 },
  pi: { last_frame_id: 'f-1', cpu_temp_c: 82, upload_format: 2, detector: 'ok', detector_reason: '', detect_ms: 150,
        model: 'yolo11n', clock_off_sec: 0.2 },
  detection: { pictures_analysed: 10, detect_ms: 90, problem: null, model: 'yolo11n' },
  ai: { ready: true, label: 'Claude', problem: null },
}

const log: LogStatus = {
  folder: '/data/log', today: { pictures_waiting: 0, events: {} }, last_compaction: null, scheduler: [], stream: [],
  camera: { source: 'pi', connected: true, fps: 3, frames_received: 10, yolo_ms: 90, model: 'yolo11n' },
}

afterEach(() => vi.resetAllMocks())

describe('System tab', () => {
  it('shows each group with its rows, coloured by level', async () => {
    vi.mocked(getHealth).mockResolvedValue(health)
    vi.mocked(getLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByRole('heading', { name: 'Pi' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Event stream' })).toBeInTheDocument()
    expect(screen.getByText('CPU °C').closest('tr')).toHaveClass('bad')
    expect(screen.getByText('/data/log')).toBeInTheDocument()
  })

  it('says what went wrong when the server does not answer', async () => {
    vi.mocked(getHealth).mockRejectedValue(new ApiError(0, NO_ANSWER))
    vi.mocked(getLogStatus).mockResolvedValue(log)
    render(<System settings={settings} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(NO_ANSWER)
  })
})
