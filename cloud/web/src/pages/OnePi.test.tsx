import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../fakeServer'
import OnePi from './OnePi'

function stateOf(removed: boolean) {
  return {
    device: { id: 'd1', name: 'Desk Pi', time_zone: 'Europe/Paris', removed },
    last_seen_at: 1700000000,
    goals: { id: 'g1', ts: 1699999000, type: 'goals', wake: '07:00', bed: '23:00', water_ml: 2000 },
    last_camera: { id: 'c1', ts: 1699999500, type: 'camera', notice: 'covered' },
    objects: [{ label: 'cup', state: 'present', since: 1699990000, updated_at: 1699990001 }],
  }
}

const EVENTS = {
  events: [
    { id: 'e1', ts: 1699990000, type: 'object_state', object: 'cup', new_state: 'present' },
    { id: 'e2', ts: 1699995000, type: 'habit', habit: 'water', action: 'sip' },
  ],
  next_since: 1699995000,
  next_after_id: 'e2',
}

function linked(extra: Record<string, unknown> = {}) {
  return fakeServer({
    'GET /v1/devices/d1/state': { body: stateOf(false) },
    'GET /v1/devices/d1/events': { body: EVENTS },
    ...extra,
  })
}

describe('OnePi', () => {
  it('shows the state, the goals, the camera notice and the newest events first', async () => {
    linked()
    render(<OnePi id="d1" />)
    expect(await screen.findByRole('heading', { name: 'Desk Pi' })).toBeInTheDocument()
    expect(screen.getByText('cup')).toBeInTheDocument()
    expect(screen.getByText(/water ml/i)).toBeInTheDocument()
    const events = await screen.findAllByRole('listitem', { name: /event/i })
    expect(events[0]).toHaveTextContent('habit')
    expect(events[1]).toHaveTextContent('object_state')
  })

  it('asks for events by the time the server gave, and passes back the place it gave to get the rest', async () => {
    const server = linked()
    render(<OnePi id="d1" />)
    await screen.findAllByRole('listitem', { name: /event/i })
    // the next page starts where the server's reply said to
    await waitFor(() => expect(server.made('GET', '/v1/devices/d1/events')).toHaveLength(2))
    const second = server.made('GET', '/v1/devices/d1/events')[1].path
    expect(second).toContain('since=1699995000')
    expect(second).toContain('after_id=e2')
    // an event the server sends twice is shown once
    expect(screen.getAllByRole('listitem', { name: /event/i })).toHaveLength(2)
  })

  it('link again uses that same device', async () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions')
      .mockReturnValue({ timeZone: 'Europe/Paris' } as Intl.ResolvedDateTimeFormatOptions)
    const server = fakeServer({
      'GET /v1/devices/d1/state': { body: stateOf(true) },
      'GET /v1/devices/d1/events': { body: { events: [], next_since: 0, next_after_id: null } },
      'POST /api/devices/code': {
        body: { code: 'WXYZ-1234', expires_at: 1900000000, server_url: 'http://192.168.1.5:8080', command: 'link --code WXYZ-1234' },
      },
    })
    render(<OnePi id="d1" />)
    await userEvent.click(await screen.findByRole('button', { name: /link again/i }))
    expect(await screen.findByText('WXYZ-1234')).toBeInTheDocument()
    expect(server.made('POST', '/api/devices/code')[0].body).toEqual({ time_zone: 'Europe/Paris', device_id: 'd1' })
  })

  it('only offers link again for a removed Pi, and only offers remove for a linked one', async () => {
    linked()
    render(<OnePi id="d1" />)
    await screen.findByRole('heading', { name: 'Desk Pi' })
    expect(screen.queryByRole('button', { name: /link again/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^remove this pi$/i })).toBeInTheDocument()
  })

  it('remove and delete are separate buttons, and each asks first', async () => {
    const server = linked({
      'DELETE /api/devices/d1': { status: 204 },
      'DELETE /api/devices/d1/data': { status: 204 },
    })
    render(<OnePi id="d1" />)
    const remove = await screen.findByRole('button', { name: /^remove this pi$/i })
    const wipe = screen.getByRole('button', { name: /delete this pi and its data/i })
    expect(remove).not.toBe(wipe)

    // Remove: asks, can be cancelled, and only then calls the server, and never touches the data
    await userEvent.click(remove)
    let ask = await screen.findByRole('alertdialog')
    expect(ask).toHaveTextContent(/keeps its history/i)
    expect(server.made('DELETE', '/api/devices/d1')).toHaveLength(0)
    await userEvent.click(within(ask).getByRole('button', { name: /cancel/i }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(server.calls.some(c => c.method === 'DELETE')).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: /^remove this pi$/i }))
    ask = await screen.findByRole('alertdialog')
    await userEvent.click(within(ask).getByRole('button', { name: /^remove$/i }))
    await waitFor(() => expect(server.made('DELETE', '/api/devices/d1')).toHaveLength(1))
    expect(server.made('DELETE', '/api/devices/d1/data')).toHaveLength(0)
  })

  it('delete with data asks first and says it can not be undone', async () => {
    const server = linked({ 'DELETE /api/devices/d1/data': { status: 204 } })
    const gone = vi.fn()
    render(<OnePi id="d1" afterDelete={gone} />)
    await userEvent.click(await screen.findByRole('button', { name: /delete this pi and its data/i }))
    const ask = await screen.findByRole('alertdialog')
    expect(ask).toHaveTextContent(/can't be undone/i)
    expect(server.calls.some(c => c.method === 'DELETE')).toBe(false)
    await userEvent.click(within(ask).getByRole('button', { name: /^delete$/i }))
    await waitFor(() => expect(server.made('DELETE', '/api/devices/d1/data')).toHaveLength(1))
    expect(server.made('DELETE', '/api/devices/d1')).toHaveLength(0)
    await waitFor(() => expect(gone).toHaveBeenCalled())
  })

  it('renames the Pi', async () => {
    const server = linked({ 'PATCH /api/devices/d1': { body: {} } })
    render(<OnePi id="d1" />)
    const box = await screen.findByLabelText(/name/i)
    await userEvent.clear(box)
    await userEvent.type(box, 'Kitchen Pi')
    await userEvent.click(screen.getByRole('button', { name: /save name/i }))
    await waitFor(() => expect(server.made('PATCH', '/api/devices/d1')[0]?.body).toEqual({ name: 'Kitchen Pi' }))
  })

  it('says it plainly when the Pi is not found', async () => {
    fakeServer({ 'GET /v1/devices/nope/state': { status: 404, body: { detail: 'Not found' } } })
    render(<OnePi id="nope" />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/i)
  })
})
