import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../fakeServer'
import Pis from './Pis'

const DEVICES = [
  { id: 'd1', name: 'Desk Pi', time_zone: 'America/New_York', created_at: 1000, last_seen_at: 1700000000, removed_at: null },
  { id: 'd2', name: 'Old Pi', time_zone: 'UTC', created_at: 900, last_seen_at: null, removed_at: 1800000000 },
]

function code(serverUrl: string) {
  return {
    code: 'ABCD-EFGH',
    expires_at: 1900000000,
    server_url: serverUrl,
    command: `uv run python scripts/link_cloud.py --server ${serverUrl} --code ABCD-EFGH`,
  }
}

describe('Pis', () => {
  it('lists the Pis, each linking to its own page, and marks a removed one', async () => {
    fakeServer({ 'GET /api/devices': { body: { devices: DEVICES } } })
    render(<Pis />)
    const first = await screen.findByRole('link', { name: /desk pi/i })
    expect(first).toHaveAttribute('href', '#/pis/d1')
    const second = screen.getByRole('link', { name: /old pi/i })
    expect(second).toHaveAttribute('href', '#/pis/d2')
    expect(within(second.closest('li')!).getByText(/removed/i)).toBeInTheDocument()
    expect(within(second.closest('li')!).getByText(/not seen yet/i)).toBeInTheDocument()
  })

  it('says there are none yet', async () => {
    fakeServer({ 'GET /api/devices': { body: { devices: [] } } })
    render(<Pis />)
    expect(await screen.findByText(/no pis linked yet/i)).toBeInTheDocument()
  })

  it('add a Pi shows the code and the command, and sends the browser time zone', async () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions')
      .mockReturnValue({ timeZone: 'Asia/Tokyo' } as Intl.ResolvedDateTimeFormatOptions)
    const server = fakeServer({
      'GET /api/devices': { body: { devices: [] } },
      'POST /api/devices/code': { body: code('http://192.168.1.20:8080') },
    })
    render(<Pis />)
    await userEvent.click(await screen.findByRole('button', { name: /add a pi/i }))
    expect(await screen.findByText('ABCD-EFGH')).toBeInTheDocument()
    expect(screen.getByText(/--server http:\/\/192\.168\.1\.20:8080 --code ABCD-EFGH/)).toBeInTheDocument()
    const posts = server.made('POST', '/api/devices/code')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toEqual({ time_zone: 'Asia/Tokyo' })
    // a code is never put in an address
    expect(server.calls.every(c => !c.path.includes('ABCD'))).toBe(true)
    expect(window.location.href).not.toContain('ABCD')
  })

  it('warns when the server address is localhost, because a Pi can not reach that', async () => {
    fakeServer({
      'GET /api/devices': { body: { devices: [] } },
      'POST /api/devices/code': { body: code('http://localhost:8080') },
    })
    render(<Pis />)
    await userEvent.click(await screen.findByRole('button', { name: /add a pi/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/localhost/i)
    expect(screen.getByRole('alert')).toHaveTextContent(/PUBLIC_URL/)
  })

  it('does not warn when the address is one a Pi can reach', async () => {
    fakeServer({
      'GET /api/devices': { body: { devices: [] } },
      'POST /api/devices/code': { body: code('http://192.168.1.20:8080') },
    })
    render(<Pis />)
    await userEvent.click(await screen.findByRole('button', { name: /add a pi/i }))
    await screen.findByText('ABCD-EFGH')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the server words when a code can not be made', async () => {
    fakeServer({
      'GET /api/devices': { body: { devices: [] } },
      'POST /api/devices/code': { status: 400, body: { detail: 'That time zone is not one we know.' } },
    })
    render(<Pis />)
    await userEvent.click(await screen.findByRole('button', { name: /add a pi/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That time zone is not one we know.')
  })
})
