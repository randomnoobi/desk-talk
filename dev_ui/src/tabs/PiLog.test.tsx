import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { DevSettings, PiLog as PiLogReply, PiLogLine } from '../api'
import { PI_LOG_SOURCE_KEY } from '../browserStorage'
import PiLog from './PiLog'

// The server is faked: only the call this tab makes is swapped out; ApiError stays the real one.
vi.mock('../api', async importOriginal => ({
  ...await importOriginal<typeof import('../api')>(),
  getPiLog: vi.fn(),
}))

const getPiLog = vi.mocked(api.getPiLog)

const SETTINGS: DevSettings = {
  poll_sec: 1,
  checks: [],
  health: { pi_disk_warn_mb: 1000, detect_warn_ms: 500 },
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
  trace_step_photos_kept: 24,
  object_states: { on_confidence: 0.5, off_confidence: 0.3, appear_sec: 2, gone_sec: 5, window_pictures: 5, vote_share: 0.6 },
  scenarios: { countdown_default_sec: 5, countdown_max_sec: 600, waiting_max: 10 },
  pi_log: {
    sources: [{ name: 'detector', label: 'Detector service' }, { name: 'camera', label: 'Camera program' }],
    keep_lines: 100,
  },
}

const NOW = 1_800_000_000

const line = (number: number, text: string, kind: PiLogLine['kind'] = 'line'): PiLogLine =>
  ({ number, text, kind, at: NOW })

function reply(over: Partial<PiLogReply> = {}): PiLogReply {
  const lines = over.lines ?? []
  return {
    source: 'detector', state: 'connected', problem: '', retry_at: null, lines,
    end: lines.length ? lines[lines.length - 1].number : 0, dropped: false, ...over,
  }
}

async function wait(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

async function show(props: { settings?: DevSettings, shown?: boolean } = {}) {
  const view = render(<PiLog settings={props.settings ?? SETTINGS} shown={props.shown ?? true} />)
  await wait(0)
  return view
}

/** The log lines on the page, in order. */
const shownLines = () =>
  [...screen.getByRole('log').querySelectorAll('[data-number]')].map(el => el.textContent)

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW * 1000)
  localStorage.clear()
  getPiLog.mockReset().mockResolvedValue(reply())
})

describe('Pi log: reading', () => {
  it('shows the lines and asks after the last end each time', async () => {
    getPiLog
      .mockResolvedValueOnce(reply({ lines: [line(1, 'detector started'), line(2, 'model loaded')] }))
      .mockResolvedValueOnce(reply({ lines: [line(3, 'picture in')] }))
    await show()
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 0)
    expect(shownLines()).toEqual(['detector started', 'model loaded'])

    await wait(SETTINGS.poll_sec * 1000)
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 2)
    expect(shownLines()).toEqual(['detector started', 'model loaded', 'picture in'])

    await wait(SETTINGS.poll_sec * 1000)
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 3)
  })

  it('keeps only settings.pi_log.keep_lines lines', async () => {
    getPiLog.mockResolvedValueOnce(reply({ lines: [1, 2, 3, 4, 5].map(n => line(n, `line ${n}`)) }))
    await show({ settings: { ...SETTINGS, pi_log: { ...SETTINGS.pi_log, keep_lines: 3 } } })
    expect(shownLines()).toEqual(['line 3', 'line 4', 'line 5'])
  })

  it('greys the notes and colours the problems', async () => {
    getPiLog.mockResolvedValueOnce(reply({
      lines: [line(1, 'ssh started', 'note'), line(2, 'all fine'), line(3, 'Permission denied (publickey)', 'problem')],
    }))
    await show()
    expect(screen.getByText('ssh started')).toHaveClass('muted')
    expect(screen.getByText('all fine')).not.toHaveClass('muted')
    expect(screen.getByText('all fine')).not.toHaveClass('error')
    expect(screen.getByText('Permission denied (publickey)')).toHaveClass('error')
  })

  it('asks from the start again when the server has started its numbers over', async () => {
    getPiLog
      .mockResolvedValueOnce(reply({ lines: [line(7, 'a'), line(8, 'b')] }))
      .mockResolvedValueOnce(reply({ lines: [], end: 2 }))
    await show()
    await wait(SETTINGS.poll_sec * 1000)
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 8)
    await wait(SETTINGS.poll_sec * 1000)
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 0)
  })

  it('empties the box when the server has started its numbers over, so the old and new lines never mix', async () => {
    getPiLog
      .mockResolvedValueOnce(reply({ lines: [line(1, 'old one'), line(2, 'old two')], dropped: true }))
      .mockResolvedValueOnce(reply({ lines: [], end: 1 }))
      .mockResolvedValueOnce(reply({ lines: [line(1, 'new one'), line(2, 'new two')] }))
    await show()
    expect(shownLines()).toEqual(['old one', 'old two'])
    expect(screen.getByText(/older lines were let go/)).toBeInTheDocument()

    await wait(SETTINGS.poll_sec * 1000)
    expect(shownLines()).toEqual([])
    expect(screen.queryByText(/older lines were let go/)).toBeNull()

    await wait(SETTINGS.poll_sec * 1000)
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 0)
    expect(shownLines()).toEqual(['new one', 'new two'])
  })

  it('says when the server did not answer, and keeps asking', async () => {
    getPiLog.mockRejectedValueOnce(new api.ApiError(0, api.NO_ANSWER))
    await show()
    expect(screen.getByRole('alert')).toHaveTextContent(api.NO_ANSWER)
    await wait(SETTINGS.poll_sec * 1000)
    expect(getPiLog).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('Pi log: how the connection is doing', () => {
  it.each([
    ['connected', null, 'Connected'],
    ['connecting', null, 'Connecting…'],
    ['waiting', NOW + 7, 'Connection lost — trying again in 7 s'],
    ['stopped', null, 'Stopped — it starts again while this tab is open'],
  ] as const)('says %s in words', async (state, retryAt, words) => {
    getPiLog.mockResolvedValue(reply({ state, retry_at: retryAt }))
    await show()
    expect(screen.getByRole('status')).toHaveTextContent(words)
  })

  it('shows the latest problem', async () => {
    getPiLog.mockResolvedValue(reply({ state: 'waiting', retry_at: NOW + 3, problem: 'Permission denied (publickey).' }))
    await show()
    expect(screen.getByText('Permission denied (publickey).')).toBeInTheDocument()
  })

  it('says when some older lines were let go', async () => {
    await show()
    expect(screen.queryByText(/older lines were let go/)).toBeNull()
    getPiLog.mockResolvedValueOnce(reply({ lines: [line(900, 'late')], dropped: true }))
    await wait(SETTINGS.poll_sec * 1000)
    expect(screen.getByText(/older lines were let go/)).toBeInTheDocument()
  })
})

describe('Pi log: the box', () => {
  it('shows only the lines with the filter text, whatever the case', async () => {
    getPiLog.mockResolvedValueOnce(reply({ lines: [line(1, 'ERROR: no camera'), line(2, 'ok'), line(3, 'an error again')] }))
    await show()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Only lines with' }), { target: { value: 'error' } })
    expect(shownLines()).toEqual(['ERROR: no camera', 'an error again'])
  })

  it('Clear empties the box but keeps reading from where it was', async () => {
    getPiLog.mockResolvedValueOnce(reply({ lines: [line(1, 'a'), line(2, 'b')], dropped: true }))
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(shownLines()).toEqual([])
    expect(screen.queryByText(/older lines were let go/)).toBeNull()

    getPiLog.mockResolvedValueOnce(reply({ lines: [line(3, 'c')] }))
    await wait(SETTINGS.poll_sec * 1000)
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 2)
    expect(shownLines()).toEqual(['c'])
  })
})

describe('Pi log: which log', () => {
  it('starts with the first source, and a new pick empties the box, asks from 0 and is remembered', async () => {
    getPiLog.mockResolvedValueOnce(reply({ lines: [line(1, 'from the detector')] }))
    await show()
    const picker = screen.getByRole('combobox', { name: 'Log' })
    expect(picker).toHaveValue('detector')

    getPiLog.mockResolvedValueOnce(reply({ source: 'camera', lines: [] }))
    fireEvent.change(picker, { target: { value: 'camera' } })
    await wait(0)
    expect(getPiLog).toHaveBeenLastCalledWith('camera', 0)
    expect(shownLines()).toEqual([])
    expect(localStorage.getItem(PI_LOG_SOURCE_KEY)).toBe('camera')
  })

  it('opens the remembered source, or the first when the remembered one is gone', async () => {
    localStorage.setItem(PI_LOG_SOURCE_KEY, 'camera')
    const first = await show()
    expect(getPiLog).toHaveBeenLastCalledWith('camera', 0)
    first.unmount()

    localStorage.setItem(PI_LOG_SOURCE_KEY, 'not any more')
    await show()
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 0)
  })

  it('says so when the settings list no logs, and asks for nothing', async () => {
    await show({ settings: { ...SETTINGS, pi_log: { ...SETTINGS.pi_log, sources: [] } } })
    expect(screen.getByText(/no Pi logs/)).toBeInTheDocument()
    expect(getPiLog).not.toHaveBeenCalled()
  })
})

describe('Pi log: only while the tab is shown', () => {
  it('asks nothing while hidden, starts once shown and stops when hidden again', async () => {
    const view = await show({ shown: false })
    await wait(5 * SETTINGS.poll_sec * 1000)
    expect(getPiLog).not.toHaveBeenCalled()

    getPiLog.mockResolvedValueOnce(reply({ lines: [line(1, 'a'), line(2, 'b')] }))
    view.rerender(<PiLog settings={SETTINGS} shown />)
    await wait(0)
    expect(getPiLog).toHaveBeenCalledTimes(1)

    view.rerender(<PiLog settings={SETTINGS} shown={false} />)
    await wait(5 * SETTINGS.poll_sec * 1000)
    expect(getPiLog).toHaveBeenCalledTimes(1)
    expect(shownLines()).toEqual(['a', 'b'])

    view.rerender(<PiLog settings={SETTINGS} shown />)
    await wait(0)
    expect(getPiLog).toHaveBeenLastCalledWith('detector', 2)
  })
})
