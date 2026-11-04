import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DevSettings, ObjectTraceReply, TracePhoto, TraceRow } from './api'
import ObjectTrace, { whatHappened, whyWords } from './ObjectTrace'

const settings = { trace_events_kept: 50, trace_photos_kept: 6, trace_step_photos_kept: 24 } as DevSettings

const row = (over: Partial<TraceRow>): TraceRow => ({
  ts: 1_760_000_000, source: 'chat', kind: 'joined', confidence: 0.81, seen_for_sec: 3.2, on_desk: true,
  step: 'Normal', before: null, after: null, photo: null, what: '', ...over,
})

const appearedPhoto: TracePhoto = {
  id: 'p1', ts: 1_760_000_000, why: 'appeared', confidence: 0.82, box: [10, 20, 50, 80], size: [640, 480],
}
const timedPhoto: TracePhoto = {
  id: 'p2', ts: 1_760_000_120, why: 'every', confidence: 0.9, box: [12, 22, 52, 82], size: [640, 480],
}

const trace: ObjectTraceReply = {
  label: 'cup',
  timeline: [
    row({ ts: 1_760_000_000, source: 'state', kind: 'appeared', confidence: 0.8, seen_for_sec: null, on_desk: null,
          step: null, before: 'ABSENT', after: 'PRESENT', photo: appearedPhoto }),
    row({ ts: 1_760_000_002, kind: 'refused', on_desk: null, step: 'Picky' }),
    row({ ts: 1_760_000_005 }),
    row({ ts: 1_760_000_120, source: 'photo', kind: 'every', confidence: 0.9, seen_for_sec: null, on_desk: null,
          step: null, photo: timedPhoto, what: 'still there' }),
  ],
  photos: [appearedPhoto, timedPhoto],
}

describe('the words', () => {
  it('says what happened in plain words', () => {
    expect(whatHappened(row({ kind: 'refused' }))).toBe('refused: not on the chat list')
    expect(whatHappened(row({ kind: 'joined' }))).toBe('joined the chat')
    expect(whatHappened(row({ source: 'state', kind: 'removed' }))).toBe('went away')
    expect(whatHappened(row({ source: 'photo', kind: 'every' }))).toBe('a picture while it was there')
    expect(whatHappened(row({ source: 'photo', kind: 'removed' }))).toBe('a picture when it went away')
  })

  it('says why: how long, how sure, where, and the sensitivity', () => {
    expect(whyWords(row({}))).toBe('seen for 3.2 s, 0.81 sure, inside the desk, sensitivity Normal')
    expect(whyWords(row({ on_desk: false, step: 'Eager' }))).toContain('outside the desk')
    expect(whyWords(row({ on_desk: null }))).toContain('no desk marked')
    expect(whyWords(trace.timeline[0])).toBe('0.80 sure on average, ABSENT → PRESENT')
    expect(whyWords(trace.timeline[3])).toBe('0.90 sure')
  })
})

describe('ObjectTrace', () => {
  const rowsOf = () => within(screen.getByRole('table')).getAllByRole('row').slice(1)

  it('shows a timeline row for each thing that happened, newest first', () => {
    render(<ObjectTrace label="cup" settings={settings} trace={trace} smoothing={<p>the chart</p>} />)
    const rows = rowsOf()
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('a picture while it was there')
    expect(rows[1]).toHaveTextContent('joined the chat')
    expect(rows[2]).toHaveTextContent('refused: not on the chat list')
    expect(rows[2]).toHaveTextContent('sensitivity Picky')
    expect(rows[3]).toHaveTextContent('appeared')
    expect(screen.getByText('the chart')).toBeInTheDocument()
  })

  it('puts each picture next to its own row, with the box drawn in the picture\'s own pixels', () => {
    render(<ObjectTrace label="cup" settings={settings} trace={trace} smoothing={null} />)
    const rows = rowsOf()
    const picture = within(rows[3]).getByRole('img', { name: /cup/ })
    expect(picture).toHaveAttribute('src', '/api/dev/objects/photo/p1')
    const svg = rows[3].querySelector('svg')
    expect(svg).toHaveAttribute('viewBox', '0 0 640 480')
    const box = rows[3].querySelector('svg rect')
    expect(box).toHaveAttribute('x', '10')
    expect(box).toHaveAttribute('y', '20')
    expect(box).toHaveAttribute('width', '40')
    expect(box).toHaveAttribute('height', '60')
    expect(within(rows[0]).getByRole('img', { name: /cup/ })).toHaveAttribute('src', '/api/dev/objects/photo/p2')
    expect(within(rows[1]).queryByRole('img')).toBeNull()             // no picture was taken for that step
  })

  it('opens a picture big when its thumbnail is clicked, and closes it again', () => {
    render(<ObjectTrace label="cup" settings={settings} trace={trace} smoothing={null} />)
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(within(rowsOf()[3]).getByRole('button', { name: /bigger/ }))
    const big = screen.getByRole('dialog', { name: /cup/ })
    expect(within(big).getByRole('img', { name: /cup/ })).toHaveAttribute('src', '/api/dev/objects/photo/p1')
    expect(big).toHaveTextContent('0.82 sure')
    fireEvent.click(within(big).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('takes how many are kept from the settings, and says when there is nothing yet', () => {
    render(<ObjectTrace label="glasses" settings={settings} trace={{ label: 'glasses', timeline: [], photos: [] }}
                        smoothing={null} />)
    expect(screen.getByText(/Nothing has happened to it/)).toBeInTheDocument()
    expect(screen.getByText(/newest 50/)).toBeInTheDocument()
    expect(screen.getByText(/newest 6 timed pictures/)).toBeInTheDocument()
    expect(screen.getByText(/newest 24 step pictures/)).toBeInTheDocument()
  })
})
