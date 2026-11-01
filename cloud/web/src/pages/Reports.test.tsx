import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../fakeServer'
import { markSeen } from '../seen'
import Reports from './Reports'

const DEVICES = { devices: [{ id: 'd1', name: 'Desk Pi', time_zone: 'UTC', created_at: 1, last_seen_at: 2, removed_at: null }] }

function report(id: string, week_start: string, status: string, extra: Record<string, unknown> = {}) {
  return { id, device_id: 'd1', week_start, status, markdown: null, model: null, error: null, created_at: 1700000000, ...extra }
}

function serverWith(reports: unknown[]) {
  return fakeServer({
    'GET /api/devices': { body: DEVICES },
    'GET /api/reports?device_id=d1': { body: { reports } },
  })
}

describe('Reports', () => {
  it('gives a new report a mark, and takes it off once the report was opened', async () => {
    serverWith([report('r2', '2026-10-19', 'done', { markdown: '# Hi' }), report('r1', '2026-10-12', 'done', { markdown: '# Old' })])
    markSeen('r1')
    const { unmount } = render(<Reports />)
    const newOne = (await screen.findByRole('link', { name: /2026-10-19/ })).closest('li')!
    const seenOne = screen.getByRole('link', { name: /2026-10-12/ }).closest('li')!
    expect(within(newOne).getByText('new')).toBeInTheDocument()
    expect(within(seenOne).queryByText('new')).not.toBeInTheDocument()
    unmount()

    markSeen('r2')            // what opening the report does
    serverWith([report('r2', '2026-10-19', 'done', { markdown: '# Hi' })])
    render(<Reports />)
    const again = (await screen.findByRole('link', { name: /2026-10-19/ })).closest('li')!
    expect(within(again).queryByText('new')).not.toBeInTheDocument()
  })

  it('still works, and keeps the mark for the visit, when the browser blocks storage', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
    serverWith([report('r9', '2026-10-19', 'done', { markdown: 'x' })])
    const { unmount } = render(<Reports />)
    expect(await within((await screen.findByRole('link', { name: /2026-10-19/ })).closest('li')!).findByText('new')).toBeInTheDocument()
    unmount()
    markSeen('r9')
    render(<Reports />)
    const li = (await screen.findByRole('link', { name: /2026-10-19/ })).closest('li')!
    expect(within(li).queryByText('new')).not.toBeInTheDocument()
  })

  it('says what happened to a failed report and a week with no data, and does not mark them new', async () => {
    serverWith([
      report('r3', '2026-10-26', 'failed', { error: 'No AI key is set for reports.' }),
      report('r2', '2026-10-19', 'no_data'),
      report('r1', '2026-10-12', 'pending'),
    ])
    render(<Reports />)
    const failed = (await screen.findByRole('link', { name: /2026-10-26/ })).closest('li')!
    expect(failed).toHaveTextContent(/couldn't be written/i)
    expect(failed).toHaveTextContent('No AI key is set for reports.')
    const none = screen.getByRole('link', { name: /2026-10-19/ }).closest('li')!
    expect(none).toHaveTextContent(/no data/i)
    const pending = screen.getByRole('link', { name: /2026-10-12/ }).closest('li')!
    expect(pending).toHaveTextContent(/being written/i)
    expect(screen.queryByText('new')).not.toBeInTheDocument()
  })

  it('says there are no reports yet', async () => {
    serverWith([])
    render(<Reports />)
    expect(await screen.findByText(/no reports yet/i)).toBeInTheDocument()
  })

  it('lists newest week first, across Pis', async () => {
    fakeServer({
      'GET /api/devices': { body: { devices: [
        { id: 'd1', name: 'Desk Pi', time_zone: 'UTC', created_at: 1, last_seen_at: 2, removed_at: null },
        { id: 'd2', name: 'Bed Pi', time_zone: 'UTC', created_at: 1, last_seen_at: 2, removed_at: null },
      ] } },
      'GET /api/reports?device_id=d1': { body: { reports: [report('a', '2026-10-12', 'done')] } },
      'GET /api/reports?device_id=d2': { body: { reports: [{ ...report('b', '2026-10-19', 'done'), device_id: 'd2' }] } },
    })
    render(<Reports />)
    const items = await screen.findAllByRole('listitem')
    expect(items[0]).toHaveTextContent('2026-10-19')
    expect(items[0]).toHaveTextContent('Bed Pi')
    expect(items[1]).toHaveTextContent('2026-10-12')
  })
})
