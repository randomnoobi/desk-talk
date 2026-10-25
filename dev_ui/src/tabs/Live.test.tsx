import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getStates, getTrace, getTracedLabels, type DevSettings, type SmoothingStep, type States } from '../api'
import Live from './Live'

vi.mock('../api')
// uPlot needs a real canvas, which jsdom doesn't have: the chart here only says what it was given.
vi.mock('../SmoothingChart', () => ({
  default: (props: { steps: SmoothingStep[] }) => <div data-testid="smoothing-chart">{props.steps.length} pictures</div>,
}))

const settings: DevSettings = {
  poll_sec: 2,
  checks: ['Fast tests'],
  health: { pi_cpu_warn_c: 70, pi_cpu_bad_c: 80, pi_disk_warn_mb: 1000, detect_warn_ms: 500 },
  trace_pictures: 90,
  sql_max_rows: 500,
  data_folder: 'data',
  object_states: { on_confidence: 0.5, off_confidence: 0.4, appear_sec: 3, gone_sec: 3, window_pictures: 5, vote_share: 0.5 },
}

const now = Date.now() / 1000
const states: States = {
  objects: { cup: { state: 'PRESENT', since: now - 30 }, 'pen/pencil': { state: 'ABSENT', since: now - 5 } },
  watched_since: now - 600,
  changes: [{ ts: now - 30, label: 'cup', change: 'appeared' }],
}

const step = (over: Partial<SmoothingStep>): SmoothingStep => ({
  ts: now, conf: 0.9, box_seen: true, votes_yes: 5, votes_total: 5, seen: true, center: [0.5, 0.5],
  phase: 'PRESENT', heading: null, waiting_sec: null, ...over,
})

beforeEach(() => {
  vi.mocked(getStates).mockResolvedValue(states)
  vi.mocked(getTracedLabels).mockResolvedValue(['cup', 'pen/pencil'])
  vi.mocked(getTrace).mockResolvedValue([step({})])
})

describe('Live tab', () => {
  it('shows a PRESENT card for cup, and its appeared change', async () => {
    render(<Live settings={settings} />)
    const card = await screen.findByRole('group', { name: 'cup' })
    expect(within(card).getByText('PRESENT')).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'pen/pencil' })).getByText('ABSENT')).toBeInTheDocument()
    expect(await screen.findByText('appeared')).toBeInTheDocument()
  })

  it('asks for the trace of pen/pencil when you pick it', async () => {
    render(<Live settings={settings} />)
    const picker = await screen.findByRole('combobox', { name: 'Object to show' })
    await within(picker).findByRole('option', { name: 'pen/pencil' })
    await userEvent.selectOptions(picker, 'pen/pencil')
    await waitFor(() => expect(getTrace).toHaveBeenCalledWith('pen/pencil'))
  })

  it('says so when the object has no pictures yet', async () => {
    vi.mocked(getTrace).mockResolvedValue([])
    render(<Live settings={settings} />)
    expect(await screen.findByText('No pictures yet for this object')).toBeInTheDocument()
    expect(screen.queryByTestId('smoothing-chart')).not.toBeInTheDocument()
  })

  it('shows the countdown while it waits', async () => {
    vi.mocked(getTrace).mockResolvedValue([step({ conf: null, box_seen: false, heading: 'ABSENT', waiting_sec: 1.5 })])
    render(<Live settings={settings} />)
    expect(await screen.findByText(/Waiting to turn ABSENT/)).toHaveTextContent('1.5 s of 3 s')
    expect(screen.getByTestId('smoothing-chart')).toHaveTextContent('1 pictures')
  })

  it('marks an object with more than 4 flips in the last minute as flickering', async () => {
    const flips = ['appeared', 'removed', 'appeared', 'removed', 'appeared'] as const
    vi.mocked(getStates).mockResolvedValue({
      ...states, changes: flips.map((change, i) => ({ ts: now - 50 + i * 5, label: 'cup', change })),
    })
    render(<Live settings={settings} />)
    const card = await screen.findByRole('group', { name: 'cup' })
    expect(await within(card).findByText('flickering')).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'pen/pencil' })).queryByText('flickering')).not.toBeInTheDocument()
  })
})
