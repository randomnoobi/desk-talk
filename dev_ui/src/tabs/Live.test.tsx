import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getObjectTrace, getStates, getTrace, getTracedLabels, type DevSettings, type SmoothingStep,
         type States } from '../api'
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
  disconnect_after_sec: 3,
  web_port: 5000,
  flicker_flips_per_min: 4,
  output_keep_kb: 2000,
  flips_window_min: 10,
  table_counts_every_sec: 30,
  trace_events_kept: 50,
  trace_photos_kept: 6,
  object_states: { on_confidence: 0.5, off_confidence: 0.4, appear_sec: 3, gone_sec: 3, window_pictures: 5, vote_share: 0.5 },
  scenarios: { countdown_default_sec: 5, countdown_max_sec: 600, waiting_max: 10 },
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
  vi.mocked(getObjectTrace).mockImplementation(async label => ({ label, timeline: [], photos: [] }))
})

describe('Live tab', () => {
  it('shows a PRESENT card for cup, and its appeared change', async () => {
    render(<Live settings={settings} />)
    const card = await screen.findByRole('button', { name: 'cup' })
    expect(within(card).getByText('PRESENT')).toBeInTheDocument()
    expect(within(screen.getByRole('button', { name: 'pen/pencil' })).getByText('ABSENT')).toBeInTheDocument()
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
    const card = await screen.findByRole('button', { name: 'cup' })
    expect(await within(card).findByText('flickering')).toBeInTheDocument()
    expect(within(screen.getByRole('button', { name: 'pen/pencil' })).queryByText('flickering')).not.toBeInTheDocument()
  })

  it('takes the flicker limit from settings', async () => {
    const flips = ['appeared', 'removed', 'appeared'] as const
    vi.mocked(getStates).mockResolvedValue({
      ...states, changes: flips.map((change, i) => ({ ts: now - 50 + i * 5, label: 'cup', change })),
    })
    render(<Live settings={{ ...settings, flicker_flips_per_min: 2 }} />)
    const card = await screen.findByRole('button', { name: 'cup' })
    expect(await within(card).findByText('flickering')).toHaveAttribute('title', 'More than 2 flips in the last minute')
  })

  it('keeps only the newest settings.sql_max_rows changes in the list', async () => {
    const moves = Array.from({ length: 20 }, (_, i) => ({ ts: now - 5000 + i, label: 'cup', change: 'moved' as const }))
    vi.mocked(getStates).mockResolvedValue({ ...states, changes: moves })
    render(<Live settings={{ ...settings, sql_max_rows: 5 }} />)
    await screen.findAllByText('moved')
    expect(screen.getAllByText('moved')).toHaveLength(5)
  })

  it('opens the trace of an object when you click its card', async () => {
    vi.mocked(getObjectTrace).mockImplementation(async label => ({
      label,
      timeline: [{ ts: now - 30, source: 'chat', kind: 'refused', confidence: 0.6, seen_for_sec: 4, on_desk: false,
                   step: 'Normal', before: null, after: null }],
      photos: [],
    }))
    render(<Live settings={settings} />)
    await userEvent.click(await screen.findByRole('button', { name: 'pen/pencil' }))
    await waitFor(() => expect(getObjectTrace).toHaveBeenCalledWith('pen/pencil'))
    const panel = await screen.findByRole('region', { name: 'Trace of pen/pencil' })
    expect(await within(panel).findByText('refused: not on the chat list')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'pen/pencil' })).toHaveAttribute('aria-pressed', 'true')
    expect(getTrace).toHaveBeenCalledWith('pen/pencil')
  })
})
