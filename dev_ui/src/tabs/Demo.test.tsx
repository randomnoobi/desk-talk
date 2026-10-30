import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { DevSettings, Scenarios, WaitingScenario } from '../api'
import Demo, { secondsLeftNow } from './Demo'

// The server is faked: only the calls this tab makes are swapped out; ApiError stays the real one.
vi.mock('../api', async importOriginal => {
  const real = await importOriginal<typeof import('../api')>()
  return { ...real, getScenarios: vi.fn(), startScenario: vi.fn(), cancelScenario: vi.fn() }
})

const getScenarios = vi.mocked(api.getScenarios)
const startScenario = vi.mocked(api.startScenario)
const cancelScenario = vi.mocked(api.cancelScenario)

const SETTINGS: DevSettings = {
  poll_sec: 60,                    // long, so the countdowns below only move by the page's own clock
  checks: ['Fast tests', 'Lint', 'Types'],
  health: { pi_cpu_warn_c: 70, pi_cpu_bad_c: 80, pi_disk_warn_mb: 1000, detect_warn_ms: 500 },
  trace_pictures: 90,
  sql_max_rows: 500,
  data_folder: '/tmp/data',
  disconnect_after_sec: 3,
  web_port: 5000,
  flicker_flips_per_min: 4,
  output_keep_kb: 2000,
  flips_window_min: 10,
  table_counts_every_sec: 30,
  trace_events_kept: 50,
  trace_photos_kept: 6,
  object_states: { on_confidence: 0.5, off_confidence: 0.3, appear_sec: 2, gone_sec: 5, window_pictures: 5, vote_share: 0.6 },
  scenarios: { countdown_default_sec: 7, countdown_max_sec: 600, waiting_max: 10 },
}

const waiting = (over: Partial<WaitingScenario> = {}): WaitingScenario => ({
  id: 'demo-1', name: 'cup_at_edge', label: 'Cup near the desk edge', fires_at: 1000, seconds_left: 10, ...over,
})

function reply(waitingNow: WaitingScenario[] = []): Scenarios {
  return {
    scenarios: [
      { name: 'cup_at_edge', label: 'Cup near the desk edge', fires: 'edge_warning' },
      { name: 'sip', label: 'A sip counted', fires: 'sip' },
    ],
    waiting: waitingNow,
  }
}

async function wait(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

async function show(settings: DevSettings = SETTINGS) {
  render(<Demo settings={settings} />)
  await wait(0)
}

beforeEach(() => {
  vi.useFakeTimers()
  getScenarios.mockReset().mockResolvedValue(reply())
  startScenario.mockReset().mockResolvedValue(waiting())
  cancelScenario.mockReset().mockResolvedValue(undefined)
})

describe('Demo', () => {
  it('lists the pretend warnings and starts the countdown at the settings default', async () => {
    await show()
    const pick = screen.getByLabelText('Pretend warning')
    expect(within(pick).getAllByRole('option').map(o => o.textContent)).toEqual(['Cup near the desk edge', 'A sip counted'])
    const seconds = screen.getByLabelText('Seconds')
    expect(seconds).toHaveValue(7)
    expect(seconds).toHaveAttribute('max', '600')
  })

  it('starts the one picked with the seconds typed in', async () => {
    await show()
    fireEvent.change(screen.getByLabelText('Pretend warning'), { target: { value: 'sip' } })
    fireEvent.change(screen.getByLabelText('Seconds'), { target: { value: '12' } })
    getScenarios.mockResolvedValue(reply([waiting({ name: 'sip', label: 'A sip counted', seconds_left: 12 })]))
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await wait(0)
    expect(startScenario).toHaveBeenCalledWith('sip', 12)
    expect(screen.getByText('A sip counted', { selector: '.waiting-label' })).toBeInTheDocument()
  })

  it('counts down each waiting one, every second', async () => {
    getScenarios.mockResolvedValue(reply([waiting({ seconds_left: 10 })]))
    await show()
    expect(screen.getByText('10 s')).toBeInTheDocument()
    await wait(3000)
    expect(screen.getByText('7 s')).toBeInTheDocument()
  })

  it('cancels one', async () => {
    getScenarios.mockResolvedValue(reply([waiting()]))
    await show()
    getScenarios.mockResolvedValue(reply())
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await wait(0)
    expect(cancelScenario).toHaveBeenCalledWith('demo-1')
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    expect(screen.getByText(/Nothing is counting down/)).toBeInTheDocument()
  })

  it("shows the server's words when a start is refused", async () => {
    await show()
    startScenario.mockRejectedValue(new api.ApiError(400, 'The countdown must be from 0 to 600 seconds.'))
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await wait(0)
    expect(screen.getByRole('alert')).toHaveTextContent('The countdown must be from 0 to 600 seconds.')
  })

  it('shows "going off" once the countdown is over, until the server says it went', async () => {
    getScenarios.mockResolvedValue(reply([waiting({ seconds_left: 2 })]))
    await show()
    await wait(5000)
    expect(screen.getByText('going off…')).toBeInTheDocument()
    expect(secondsLeftNow(2, 5000)).toBe(0)
    expect(secondsLeftNow(2.4, 1000)).toBe(2)
  })

  it('says these are pretend warnings that really go out', async () => {
    await show()
    expect(screen.getByText(/go out like real ones/)).toBeInTheDocument()
  })
})
