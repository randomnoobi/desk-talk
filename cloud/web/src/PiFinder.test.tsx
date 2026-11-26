import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { fakeServer, homeNetwork, type Reply } from './fakeServer'
import { PiFinder } from './PiFinder'

const FOUND = [
  { address: '192.168.86.225', name: 'desktalk', linked: false },
  { address: '192.168.86.240', name: 'kitchen', linked: true },
]

// A reply that waits until the test lets it go, so the page can be looked at while it waits.
function held(reply: Reply) {
  let release = () => {}
  const answer = new Promise<Reply>(resolve => { release = () => resolve(reply) })
  return { answer: () => answer, release: () => release() }
}

function rowOf(name: string) {
  return screen.getByText(name).closest('li')!
}

describe('PiFinder', () => {
  it('has a button to search, and says it is looking while it waits', async () => {
    const where = held(homeNetwork([])['GET /api/pis/where_to_look'] as Reply)
    fakeServer({ ...homeNetwork(FOUND), 'GET /api/pis/where_to_look': where.answer })
    render(<PiFinder onLinked={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    expect(await screen.findByText('Looking on your home network…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Find Pis on my network' })).toBeDisabled()
    where.release()
    expect(await screen.findByText('desktalk')).toBeInTheDocument()
    expect(screen.queryByText('Looking on your home network…')).not.toBeInTheDocument()
  })

  it('lists each Pi found with its address, and a linked one has no Link button', async () => {
    fakeServer(homeNetwork(FOUND))
    render(<PiFinder onLinked={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    await screen.findByText('desktalk')
    const fresh = rowOf('desktalk')
    expect(within(fresh).getByText('192.168.86.225')).toBeInTheDocument()
    expect(within(fresh).getByRole('button', { name: /link/i })).toBeInTheDocument()
    const linked = rowOf('kitchen')
    expect(within(linked).getByText('192.168.86.240')).toBeInTheDocument()
    expect(within(linked).getByText('Already linked')).toBeInTheDocument()
    expect(within(linked).queryByRole('button')).not.toBeInTheDocument()
  })

  it('Link sends the address and the browser time zone, then says linked and tells the page', async () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions')
      .mockReturnValue({ timeZone: 'Asia/Tokyo' } as Intl.ResolvedDateTimeFormatOptions)
    const server = fakeServer({
      ...homeNetwork(FOUND),
      'POST /api/pis/link': { body: { linked: true } },
    })
    const onLinked = vi.fn()
    render(<PiFinder onLinked={onLinked} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    await screen.findByText('desktalk')
    await userEvent.click(within(rowOf('desktalk')).getByRole('button', { name: /link/i }))
    expect(await within(rowOf('desktalk')).findByText('Linked')).toBeInTheDocument()
    expect(within(rowOf('desktalk')).queryByRole('button')).not.toBeInTheDocument()
    expect(onLinked).toHaveBeenCalledTimes(1)
    const posts = server.made('POST', '/api/pis/link')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toEqual({ address: '192.168.86.225', time_zone: 'Asia/Tokyo' })
  })

  it('says no Pi answered, and points at the command below', async () => {
    fakeServer(homeNetwork([]))
    render(<PiFinder onLinked={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    const line = await screen.findByText(/no pi answered/i)
    expect(line).toHaveTextContent(/same wi-fi/i)
    expect(line).toHaveTextContent(/command below/i)
  })

  it('shows the server words when there is no home network to search', async () => {
    fakeServer({
      'GET /api/pis/where_to_look': { status: 409, body: { detail: 'Set PUBLIC_URL to this computer\'s home network number.' } },
    })
    render(<PiFinder onLinked={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Set PUBLIC_URL to this computer\'s home network number.')
    expect(screen.queryByText(/no pi answered/i)).not.toBeInTheDocument()
  })

  it('looks again from the start when the button is clicked again', async () => {
    const server = fakeServer(homeNetwork(FOUND))
    render(<PiFinder onLinked={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    await screen.findByText('desktalk')
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    await screen.findByText('desktalk')
    expect(server.made('GET', '/api/pis/where_to_look')).toHaveLength(2)
  })

  it.each([
    [409, 'That Pi is already linked.'],
    [502, 'The Pi said no: That code is wrong'],
  ])('shows the server words when a link fails with %i', async (status, words) => {
    const onLinked = vi.fn()
    fakeServer({
      ...homeNetwork(FOUND),
      'POST /api/pis/link': { status, body: { detail: words } },
    })
    render(<PiFinder onLinked={onLinked} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    await screen.findByText('desktalk')
    await userEvent.click(within(rowOf('desktalk')).getByRole('button', { name: /link/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(words)
    expect(onLinked).not.toHaveBeenCalled()
    // the person can try again
    expect(within(rowOf('desktalk')).getByRole('button', { name: /link/i })).toBeEnabled()
  })

  it('a Link button can not be clicked twice while its link is going', async () => {
    const link = held({ body: { linked: true } })
    const server = fakeServer({
      ...homeNetwork(FOUND),
      'POST /api/pis/link': link.answer,
    })
    render(<PiFinder onLinked={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    await screen.findByText('desktalk')
    const button = within(rowOf('desktalk')).getByRole('button', { name: /link/i })
    await userEvent.click(button)
    expect(button).toBeDisabled()
    await userEvent.click(button)
    link.release()
    expect(await within(rowOf('desktalk')).findByText('Linked')).toBeInTheDocument()
    expect(server.made('POST', '/api/pis/link')).toHaveLength(1)
  })

  it('puts no code or address in a URL', async () => {
    const server = fakeServer({
      ...homeNetwork(FOUND),
      'POST /api/pis/link': { body: { linked: true, code: 'ABCD-EFGH' } },
    })
    render(<PiFinder onLinked={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Find Pis on my network' }))
    await screen.findByText('desktalk')
    await userEvent.click(within(rowOf('desktalk')).getByRole('button', { name: /link/i }))
    await waitFor(() => expect(server.made('POST', '/api/pis/link')).toHaveLength(1))
    // The server is told the address only in the link's body; the knocks go to the addresses themselves.
    expect(server.toServer().every(c => !c.path.includes('?'))).toBe(true)
    expect(server.toServer().every(c => !c.path.includes('192.168'))).toBe(true)
    expect(server.calls.every(c => !c.path.includes('ABCD'))).toBe(true)
    expect(window.location.href).not.toContain('192.168')
    expect(window.location.href).not.toContain('ABCD')
    expect(document.body).not.toHaveTextContent('ABCD')
  })
})
