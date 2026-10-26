import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DevSettings, ObjectTraceReply, TraceRow } from './api'
import ObjectTrace, { whatHappened, whyWords } from './ObjectTrace'

const settings = { trace_events_kept: 50, trace_photos_kept: 6 } as DevSettings

const row = (over: Partial<TraceRow>): TraceRow => ({
  ts: 1_760_000_000, source: 'chat', kind: 'joined', confidence: 0.81, seen_for_sec: 3.2, on_desk: true,
  step: 'Normal', before: null, after: null, ...over,
})

const trace: ObjectTraceReply = {
  label: 'cup',
  timeline: [
    row({ ts: 1_760_000_000, source: 'state', kind: 'appeared', confidence: 0.8, seen_for_sec: null, on_desk: null,
          step: null, before: 'ABSENT', after: 'PRESENT' }),
    row({ ts: 1_760_000_002, kind: 'refused', on_desk: null, step: 'Picky' }),
    row({ ts: 1_760_000_005 }),
  ],
  photos: [{ id: 'p1', ts: 1_760_000_000, why: 'appeared', confidence: 0.82, box: [10, 20, 50, 80], size: [640, 480] }],
}

describe('the words', () => {
  it('says what happened in plain words', () => {
    expect(whatHappened(row({ kind: 'refused' }))).toBe('refused: not on the chat list')
    expect(whatHappened(row({ kind: 'joined' }))).toBe('joined the chat')
    expect(whatHappened(row({ source: 'state', kind: 'removed' }))).toBe('went away')
  })

  it('says why: how long, how sure, where, and the sensitivity', () => {
    expect(whyWords(row({}))).toBe('seen for 3.2 s, 0.81 sure, inside the desk, sensitivity Normal')
    expect(whyWords(row({ on_desk: false, step: 'Eager' }))).toContain('outside the desk')
    expect(whyWords(row({ on_desk: null }))).toContain('no desk marked')
    expect(whyWords(trace.timeline[0])).toBe('0.80 sure on average, ABSENT → PRESENT')
  })
})

describe('ObjectTrace', () => {
  it('shows a timeline row for each thing that happened, newest first', () => {
    render(<ObjectTrace label="cup" settings={settings} trace={trace} smoothing={<p>the chart</p>} />)
    const table = screen.getByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('joined the chat')
    expect(rows[1]).toHaveTextContent('refused: not on the chat list')
    expect(rows[1]).toHaveTextContent('sensitivity Picky')
    expect(rows[2]).toHaveTextContent('appeared')
    expect(screen.getByText('the chart')).toBeInTheDocument()
  })

  it('draws the box on the picture, in the picture\'s own pixels', () => {
    const { container } = render(<ObjectTrace label="cup" settings={settings} trace={trace} smoothing={null} />)
    const picture = screen.getByRole('img', { name: /cup/ })
    expect(picture).toHaveAttribute('src', '/api/dev/objects/photo/p1')
    const svg = container.querySelector('svg')
    expect(svg).toHaveAttribute('viewBox', '0 0 640 480')
    const box = container.querySelector('svg rect')
    expect(box).toHaveAttribute('x', '10')
    expect(box).toHaveAttribute('y', '20')
    expect(box).toHaveAttribute('width', '40')
    expect(box).toHaveAttribute('height', '60')
    expect(screen.getByText(/when it appeared/)).toHaveTextContent('0.82 sure')
  })

  it('takes how many are kept from the settings, and says when there is nothing yet', () => {
    render(<ObjectTrace label="glasses" settings={settings} trace={{ label: 'glasses', timeline: [], photos: [] }}
                        smoothing={null} />)
    expect(screen.getByText(/Nothing has happened to it/)).toBeInTheDocument()
    expect(screen.getByText(/No pictures of it yet/)).toBeInTheDocument()
    expect(screen.getByText(/newest 50/)).toBeInTheDocument()
    expect(screen.getByText(/newest 6/)).toBeInTheDocument()
  })
})
