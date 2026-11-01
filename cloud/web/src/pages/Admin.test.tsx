import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { fakeServer } from '../fakeServer'
import Admin from './Admin'

const OVERVIEW = {
  users: [{ id: 'u1', email: 'sam@example.com', name: 'Sam', created_at: 1700000000 }],
  devices: [
    { id: 'd1', user_id: 'u1', name: 'Desk Pi', last_seen_at: 1700000100, removed_at: null },
    { id: 'd2', user_id: 'u1', name: 'Old Pi', last_seen_at: null, removed_at: 1700000200 },
  ],
  failed_reports: [{ id: 'r1', device_id: 'd1', week_start: '2026-10-19', error: 'No AI key is set for reports.' }],
  recent_reports: [
    { id: 'r2', device_id: 'd1', week_start: '2026-10-12', status: 'done', markdown: 'SECRET report text' },
    { id: 'r3', device_id: 'd2', week_start: '2026-10-05', status: 'no_data' },
  ],
}

describe('Admin', () => {
  it('shows who is here, whether each Pi is working, and failed reports', async () => {
    fakeServer({ 'GET /api/admin/overview': { body: OVERVIEW } })
    render(<Admin />)
    expect(await screen.findByText('sam@example.com')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: /^pis$/i })).getByText('Desk Pi')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: /^pis$/i })).getByText('Old Pi').closest('li')).toHaveTextContent(/removed/i)
    const failed = screen.getByText('2026-10-19').closest('li')!
    expect(failed).toHaveTextContent('No AI key is set for reports.')
  })

  it('has no events and no report text: it never asks for them', async () => {
    const server = fakeServer({ 'GET /api/admin/overview': { body: OVERVIEW } })
    render(<Admin />)
    await screen.findByText('sam@example.com')
    expect(server.calls.map(c => c.path)).toEqual(['/api/admin/overview'])
    expect(screen.queryByText(/events/i)).not.toBeInTheDocument()
  })

  it('re-runs a failed report', async () => {
    const server = fakeServer({
      'GET /api/admin/overview': { body: OVERVIEW },
      'POST /api/admin/reports/r1/rerun': { body: {} },
    })
    render(<Admin />)
    const row = (await screen.findByText('2026-10-19')).closest('li')!
    await userEvent.click(within(row).getByRole('button', { name: /run again/i }))
    await waitFor(() => expect(server.made('POST', '/api/admin/reports/r1/rerun')).toHaveLength(1))
  })

  it('can run a finished report again, and never shows report text', async () => {
    const server = fakeServer({
      'GET /api/admin/overview': { body: OVERVIEW },
      'POST /api/admin/reports/r2/rerun': { body: {} },
    })
    render(<Admin />)
    const row = (await screen.findByText('2026-10-12')).closest('li')!
    expect(row).toHaveTextContent('Desk Pi')
    expect(row).toHaveTextContent(/written/i)
    expect(screen.getByText('2026-10-05').closest('li')).toHaveTextContent(/no data/i)
    expect(screen.queryByText(/SECRET/)).not.toBeInTheDocument()
    await userEvent.click(within(row).getByRole('button', { name: /run again/i }))
    await waitFor(() => expect(server.made('POST', '/api/admin/reports/r2/rerun')).toHaveLength(1))
  })

  it('shows the words the server gives when a report can not be run again', async () => {
    const words = "This Pi was removed, so its report can't be run again. Link the Pi again first."
    fakeServer({
      'GET /api/admin/overview': { body: OVERVIEW },
      'POST /api/admin/reports/r3/rerun': { status: 409, body: { detail: words } },
    })
    render(<Admin />)
    const row = (await screen.findByText('2026-10-05')).closest('li')!
    await userEvent.click(within(row).getByRole('button', { name: /run again/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(words)
  })

  it('asks for the admin login first, and sends it in the body, never the address', async () => {
    let signedIn = false
    const server = fakeServer({
      'GET /api/admin/overview': () => (signedIn ? { body: OVERVIEW } : { status: 401, body: { detail: 'Log in as admin.' } }),
      'POST /auth/admin/login': () => { signedIn = true; return { body: {} } },
    })
    render(<Admin />)
    await userEvent.type(await screen.findByLabelText(/username/i), 'boss')
    const password = screen.getByLabelText(/password/i)
    expect(password).toHaveAttribute('type', 'password')
    await userEvent.type(password, 'hunter2')
    await userEvent.click(screen.getByRole('button', { name: /log in/i }))
    expect(await screen.findByText('sam@example.com')).toBeInTheDocument()
    expect(server.made('POST', '/auth/admin/login')[0].body).toEqual({ username: 'boss', password: 'hunter2' })
    expect(server.calls.every(c => !c.path.includes('hunter2'))).toBe(true)
  })

  it('shows the server words when the admin login is wrong', async () => {
    fakeServer({
      'GET /api/admin/overview': { status: 401, body: { detail: 'Log in as admin.' } },
      'POST /auth/admin/login': { status: 401, body: { detail: 'Wrong username or password.' } },
    })
    render(<Admin />)
    await userEvent.type(await screen.findByLabelText(/username/i), 'boss')
    await userEvent.type(screen.getByLabelText(/password/i), 'nope')
    await userEvent.click(screen.getByRole('button', { name: /log in/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong username or password.')
  })
})
