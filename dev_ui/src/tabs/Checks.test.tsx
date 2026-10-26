import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { DevSettings, RunStatus, TestItem, TestOutcome } from '../api'
import Checks from './Checks'

// The server is faked: only the calls this tab makes are swapped out; ApiError stays the real one.
vi.mock('../api', async importOriginal => {
  const real = await importOriginal<typeof import('../api')>()
  return {
    ...real,
    getTests: vi.fn(),
    startCheck: vi.fn(),
    startTests: vi.fn(),
    getRun: vi.fn(),
    stopRun: vi.fn(),
  }
})

const getTests = vi.mocked(api.getTests)
const startCheck = vi.mocked(api.startCheck)
const startTests = vi.mocked(api.startTests)
const getRun = vi.mocked(api.getRun)
const stopRun = vi.mocked(api.stopRun)

const SETTINGS: DevSettings = {
  poll_sec: 1,
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
}

const TESTS: TestItem[] = [
  { id: 'tests/unit/test_a.py::test_x', file: 'tests/unit/test_a.py', markers: [] },
  { id: 'tests/unit/test_a.py::test_y', file: 'tests/unit/test_a.py', markers: [] },
  { id: 'tests/slow/test_s.py::test_big', file: 'tests/slow/test_s.py', markers: ['slow'] },
]

function run(over: Partial<RunStatus> = {}): RunStatus {
  return {
    run_id: 'r1', what: 'the check', state: 'running', started: 1000, finished: null,
    steps: [], outcomes: [], output: '', output_end: 0, ...over,
  }
}

const outcome = (id: string, result: TestOutcome['outcome'], message = ''): TestOutcome =>
  ({ id, outcome: result, duration_sec: 0.1, message })

async function wait(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

async function show(settings: DevSettings = SETTINGS) {
  render(<Checks settings={settings} />)
  await wait(0)
}

beforeEach(() => {
  vi.useFakeTimers()
  getTests.mockReset().mockResolvedValue(TESTS)
  startCheck.mockReset().mockResolvedValue('r1')
  startTests.mockReset().mockResolvedValue('r2')
  getRun.mockReset().mockResolvedValue(run())
  stopRun.mockReset().mockResolvedValue(undefined)
})

describe('Checks: the one button', () => {
  it('starts the check once and greys out the Run buttons while it goes', async () => {
    await show()
    const button = screen.getByRole('button', { name: 'Run everything' })
    fireEvent.click(button)
    await wait(0)

    expect(startCheck).toHaveBeenCalledTimes(1)
    expect(getRun).toHaveBeenCalledWith('r1', 0)
    expect(button).toBeDisabled()
    for (const each of screen.getAllByRole('button', { name: /^Run / })) expect(each).toBeDisabled()
  })

  it('shows a chip for every step, waiting until the server says otherwise', async () => {
    getRun.mockResolvedValue(run({
      steps: [{ name: 'Fast tests', state: 'passed', exit_code: 0 }, { name: 'Lint', state: 'running', exit_code: null }],
    }))
    await show()
    const chips = screen.getByRole('list', { name: 'Steps' })
    expect(within(chips).getAllByRole('listitem').map(c => c.textContent)).toEqual([
      'Fast tests: waiting', 'Lint: waiting', 'Types: waiting',
    ])

    fireEvent.click(screen.getByRole('button', { name: 'Run everything' }))
    await wait(0)
    expect(within(chips).getAllByRole('listitem').map(c => c.textContent)).toEqual([
      'Fast tests: passed', 'Lint: running', 'Types: waiting',
    ])
  })

  it('says so when a run is already going', async () => {
    startCheck.mockRejectedValue(new api.ApiError(409, 'A run is already going.'))
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Run everything' }))
    await wait(0)
    expect(screen.getByRole('alert')).toHaveTextContent('A run is already going')
    expect(screen.getByRole('button', { name: 'Run everything' })).toBeEnabled()
  })
})

describe('Checks: during and after a run', () => {
  it('asks only for the new printout each time and adds it on, with a live count', async () => {
    getRun
      .mockResolvedValueOnce(run({ output: 'first ', output_end: 6, outcomes: [outcome('tests/unit/test_a.py::test_x', 'passed')] }))
      .mockResolvedValueOnce(run({
        output: 'second', output_end: 12,
        outcomes: [outcome('tests/unit/test_a.py::test_x', 'passed'), outcome('tests/unit/test_a.py::test_y', 'skipped')],
      }))
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Run everything' }))
    await wait(0)
    expect(screen.getByLabelText('Printout')).toHaveTextContent('first')

    await wait(SETTINGS.poll_sec * 1000)
    expect(getRun).toHaveBeenLastCalledWith('r1', 6)
    expect(screen.getByLabelText('Printout')).toHaveTextContent('first second')
    expect(screen.getByLabelText('Count')).toHaveTextContent('1 passed')
    expect(screen.getByLabelText('Count')).toHaveTextContent('0 failed')
    expect(screen.getByLabelText('Count')).toHaveTextContent('1 skipped')
  })

  it('keeps only the last settings.output_keep_kb of the printout (a character counted as a byte)', async () => {
    getRun
      .mockResolvedValueOnce(run({ output: `START${'a'.repeat(1000)}`, output_end: 1005 }))
      .mockResolvedValueOnce(run({ output: `${'b'.repeat(1000)}END`, output_end: 2008 }))
    await show({ ...SETTINGS, output_keep_kb: 1 })
    fireEvent.click(screen.getByRole('button', { name: 'Run everything' }))
    await wait(0)
    await wait(SETTINGS.poll_sec * 1000)

    const printout = screen.getByLabelText('Printout').textContent ?? ''
    expect(printout).toHaveLength(1024)
    expect(printout.endsWith('END')).toBe(true)
    expect(printout).not.toContain('START')
  })

  it('stops asking once the run is over', async () => {
    getRun.mockResolvedValue(run({ state: 'passed', finished: 1010 }))
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Run everything' }))
    await wait(0)
    await wait(SETTINGS.poll_sec * 5000)
    expect(getRun).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Run everything' })).toBeEnabled()
  })

  it('has a Stop button while it goes', async () => {
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Run everything' }))
    await wait(0)
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    await wait(0)
    expect(stopRun).toHaveBeenCalledWith('r1')
  })

  it('lists a failed test with its message, and Run again runs just the failed ones', async () => {
    getRun.mockResolvedValue(run({
      state: 'failed', finished: 1010,
      outcomes: [
        outcome('tests/unit/test_a.py::test_x', 'passed'),
        outcome('tests/unit/test_a.py::test_y', 'failed', 'assert 1 == 2'),
      ],
    }))
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Run everything' }))
    await wait(0)

    const failed = screen.getByRole('region', { name: 'Failed tests' })
    expect(within(failed).getByText('tests/unit/test_a.py::test_y')).toBeInTheDocument()
    expect(within(failed).getByText('assert 1 == 2')).toBeInTheDocument()
    expect(within(failed).queryByText('tests/unit/test_a.py::test_x')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Run again' }))
    await wait(0)
    expect(startTests).toHaveBeenCalledWith(['tests/unit/test_a.py::test_y'])
  })
})

describe('Checks: each test', () => {
  it('shows the tests by folder and file, files shut until you open them, with a slow tag', async () => {
    await show()
    const tree = screen.getByRole('tree')
    expect(within(tree).queryByText('test_x')).not.toBeInTheDocument()
    fireEvent.click(within(tree).getByRole('button', { name: 'test_a.py' }))
    fireEvent.click(within(tree).getByRole('button', { name: 'test_s.py' }))
    expect(within(tree).getByText('unit')).toBeInTheDocument()
    expect(within(tree).getByText('test_a.py')).toBeInTheDocument()
    expect(within(tree).getByText('test_x')).toBeInTheDocument()
    expect(within(tree).getByText('slow', { selector: '.tag' })).toBeInTheDocument()
  })

  it('a folder\'s Run button sends every test id in it', async () => {
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Run unit' }))
    await wait(0)
    expect(startTests).toHaveBeenCalledWith(['tests/unit/test_a.py::test_x', 'tests/unit/test_a.py::test_y'])
    expect(getRun).toHaveBeenCalledWith('r2', 0)
  })

  it('one test\'s Run button sends just its id', async () => {
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'test_s.py' }))
    fireEvent.click(screen.getByRole('button', { name: 'Run test_big' }))
    await wait(0)
    expect(startTests).toHaveBeenCalledWith(['tests/slow/test_s.py::test_big'])
  })

  it('the search box keeps only the tests that match', async () => {
    await show()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a test' }), { target: { value: 'test_y' } })
    const tree = screen.getByRole('tree')
    expect(within(tree).getByText('test_y')).toBeInTheDocument()
    expect(within(tree).queryByText('test_x')).not.toBeInTheDocument()
    expect(within(tree).queryByText('slow')).not.toBeInTheDocument()
  })

  it('colours each test as it finishes, and opens the file of a failed one', async () => {
    getRun.mockResolvedValue(run({ outcomes: [outcome('tests/unit/test_a.py::test_x', 'failed', 'boom')] }))
    await show()
    fireEvent.click(screen.getByRole('button', { name: 'Run everything' }))
    await wait(0)
    const row = screen.getByRole('treeitem', { name: 'test_x' })
    expect(row).toHaveClass('bad')
    expect(screen.getByRole('treeitem', { name: 'test_y' })).not.toHaveClass('bad')
  })

  it('says what went wrong when the list can\'t be had', async () => {
    getTests.mockRejectedValue(new api.ApiError(0, api.NO_ANSWER))
    await show()
    expect(screen.getByRole('alert')).toHaveTextContent(api.NO_ANSWER)
    expect(screen.getByRole('button', { name: 'Run everything' })).toBeEnabled()
  })
})
