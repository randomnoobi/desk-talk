import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../fakeServer'
import Login from './Login'

describe('Login', () => {
  it('has a Google button', () => {
    render(<Login />)
    const button = screen.getByRole('link', { name: /log in with google/i })
    expect(button).toHaveAttribute('href', '/auth/google/login')
  })

  it('goes to Google when the server is ready', async () => {
    fakeServer({ 'GET /auth/google/login': { status: 302 } })
    const go = vi.fn()
    render(<Login go={go} />)
    await userEvent.click(screen.getByRole('link', { name: /log in with google/i }))
    await waitFor(() => expect(go).toHaveBeenCalledWith('/auth/google/login'))
  })

  it('shows the setup message when Google is not set up', async () => {
    const words = 'Google login is not set up yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to cloud/.env.'
    fakeServer({ 'GET /auth/google/login': { status: 503, body: { detail: words } } })
    const go = vi.fn()
    render(<Login go={go} />)
    await userEvent.click(screen.getByRole('link', { name: /log in with google/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(words)
    expect(go).not.toHaveBeenCalled()
  })

  it('says so when the server cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    render(<Login go={vi.fn()} />)
    await userEvent.click(screen.getByRole('link', { name: /log in with google/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/can't reach the server/i)
  })
})
