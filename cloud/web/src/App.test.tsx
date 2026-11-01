import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from './App'
import { fakeServer } from './fakeServer'

const ME = { id: 'u1', email: 'sam@example.com', name: 'Sam' }
const DEVICES = { devices: [{ id: 'd1', name: 'Desk Pi', time_zone: 'UTC', created_at: 1, last_seen_at: 2, removed_at: null }] }

describe('App', () => {
  it('shows the login page when nobody is logged in', async () => {
    fakeServer({ 'GET /api/me': { status: 401, body: { detail: 'Log in first.' } } })
    render(<App />)
    expect(await screen.findByRole('link', { name: /log in with google/i })).toBeInTheDocument()
  })

  it('shows the Pis once logged in, and moves between pages with the address bar', async () => {
    fakeServer({
      'GET /api/me': { body: ME },
      'GET /api/devices': { body: DEVICES },
      'GET /api/reports?device_id=d1': { body: { reports: [] } },
    })
    render(<App />)
    expect(await screen.findByRole('link', { name: /desk pi/i })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: /^reports/i }))
    expect(window.location.hash).toBe('#/reports')
    expect(await screen.findByText(/no reports yet/i)).toBeInTheDocument()
  })

  it('marks Reports in the menu when there is a report not read yet', async () => {
    fakeServer({
      'GET /api/me': { body: ME },
      'GET /api/devices': { body: DEVICES },
      'GET /api/reports?device_id=d1': { body: { reports: [{ id: 'r1', device_id: 'd1', week_start: '2026-10-19', status: 'done', markdown: 'x', model: null, error: null, created_at: 1 }] } },
    })
    render(<App />)
    expect(await screen.findByRole('link', { name: /^reports.*new/i })).toBeInTheDocument()
  })

  it('shows the admin page without a person logged in', async () => {
    window.location.hash = '#/admin'
    fakeServer({ 'GET /api/admin/overview': { status: 401, body: { detail: 'Log in as admin.' } } })
    render(<App />)
    expect(await screen.findByLabelText(/username/i)).toBeInTheDocument()
  })

  it('logs out', async () => {
    const server = fakeServer({
      'GET /api/me': { body: ME },
      'GET /api/devices': { body: DEVICES },
      'GET /api/reports?device_id=d1': { body: { reports: [] } },
      'POST /auth/logout': { body: {} },
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: /log out/i }))
    expect(await screen.findByRole('link', { name: /log in with google/i })).toBeInTheDocument()
    expect(server.made('POST', '/auth/logout')).toHaveLength(1)
  })

  it('asks before deleting the account', async () => {
    const server = fakeServer({
      'GET /api/me': { body: ME },
      'GET /api/devices': { body: DEVICES },
      'GET /api/reports?device_id=d1': { body: { reports: [] } },
      'DELETE /api/me': { status: 204 },
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: /delete my account/i }))
    const ask = await screen.findByRole('alertdialog')
    expect(server.made('DELETE', '/api/me')).toHaveLength(0)
    await userEvent.click(within(ask).getByRole('button', { name: /cancel/i }))
    expect(server.made('DELETE', '/api/me')).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: /delete my account/i }))
    await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: /^delete$/i }))
    expect(await screen.findByRole('link', { name: /log in with google/i })).toBeInTheDocument()
    expect(server.made('DELETE', '/api/me')).toHaveLength(1)
  })
})
