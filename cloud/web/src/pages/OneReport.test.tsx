import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fakeServer } from '../fakeServer'
import { isSeen } from '../seen'
import OneReport from './OneReport'

function report(status: string, extra: Record<string, unknown> = {}) {
  return { id: 'r1', device_id: 'd1', week_start: '2026-10-19', status, markdown: null, model: null, error: null, created_at: 1700000000, ...extra }
}

describe('OneReport', () => {
  it('shows the markdown as a page, and remembers it was opened', async () => {
    fakeServer({ 'GET /api/reports/r1': { body: report('done', { markdown: '# Your week\n\nYou **drank** water.\n\n- one\n- two' }) } })
    render(<OneReport id="r1" />)
    expect(await screen.findByRole('heading', { name: 'Your week' })).toBeInTheDocument()
    expect(screen.getByText('drank').tagName).toBe('STRONG')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    await waitFor(() => expect(isSeen('r1')).toBe(true))
  })

  it('shows no raw HTML: tags come out as plain text and nothing runs', async () => {
    const markdown = 'Hello <script>window.hacked = 1</script>\n\n<img src="https://example.com/x.png" onerror="window.hacked = 2">\n\n<b onclick="x()">bold</b>\n\n![pic](https://example.com/p.png)'
    const { container } = render(<OneReportWith markdown={markdown} />)
    await screen.findByText(/Hello/)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(container.querySelector('[onclick]')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
    expect((window as unknown as { hacked?: number }).hacked).toBeUndefined()
  })

  it('keeps links safe: opens in a new tab with no opener, and drops javascript: addresses', async () => {
    const { container } = render(<OneReportWith markdown={'[fine](https://example.com/a) and [bad](javascript:alert(1))'} />)
    const fine = await screen.findByRole('link', { name: 'fine' })
    expect(fine).toHaveAttribute('href', 'https://example.com/a')
    expect(fine).toHaveAttribute('target', '_blank')
    expect(fine.getAttribute('rel')).toMatch(/noopener/)
    expect(fine.getAttribute('rel')).toMatch(/noreferrer/)
    for (const a of Array.from(container.querySelectorAll('a'))) expect(a.getAttribute('href') ?? '').not.toMatch(/^javascript:/i)
  })

  it('says a report could not be written, and shows the reason', async () => {
    fakeServer({ 'GET /api/reports/r1': { body: report('failed', { error: 'No AI key is set for reports.' }) } })
    render(<OneReport id="r1" />)
    expect(await screen.findByText(/couldn't be written/i)).toBeInTheDocument()
    expect(screen.getByText('No AI key is set for reports.')).toBeInTheDocument()
    expect(isSeen('r1')).toBe(false)
  })

  it('says a week had no data', async () => {
    fakeServer({ 'GET /api/reports/r1': { body: report('no_data') } })
    render(<OneReport id="r1" />)
    expect(await screen.findByText(/no data/i)).toBeInTheDocument()
  })

  it('says a report is still being written', async () => {
    fakeServer({ 'GET /api/reports/r1': { body: report('pending') } })
    render(<OneReport id="r1" />)
    expect(await screen.findByText(/being written/i)).toBeInTheDocument()
  })

  it('says it plainly when the report is not found', async () => {
    fakeServer({ 'GET /api/reports/r1': { status: 404, body: { detail: 'Not found' } } })
    render(<OneReport id="r1" />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/i)
  })
})

function OneReportWith({ markdown }: { markdown: string }) {
  fakeServer({ 'GET /api/reports/r1': { body: report('done', { markdown }) } })
  return <OneReport id="r1" />
}
