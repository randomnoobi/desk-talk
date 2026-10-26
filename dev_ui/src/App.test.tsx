import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { DevSettings } from './api'
import App, { FIRST_TRY_AGAIN_MS } from './App'

const SETTINGS: DevSettings = {
  poll_sec: 1,
  checks: ['Fast tests', 'Lint', 'Types'],
  health: { pi_cpu_warn_c: 70, pi_cpu_bad_c: 80, pi_disk_warn_mb: 1000, detect_warn_ms: 500 },
  trace_pictures: 90,
  sql_max_rows: 500,
  data_folder: '/tmp/data',
  disconnect_after_sec: 3,
  web_port: 5000,
  flicker_flips_per_min: 4,
  output_keep_kb: 2000,
  flips_window_min: 10,
  table_counts_every_sec: 30,
  trace_events_kept: 50,
  trace_photos_kept: 6,
  object_states: { on_confidence: 0.5, off_confidence: 0.3, appear_sec: 2, gone_sec: 5, window_pictures: 5, vote_share: 0.6 },
}

// A fake server: /api/dev/settings answers while `up` is true; everything else is a 404, so the tabs'
// own calls (added later) can't break these tests.
function fakeServer() {
  const server = { up: true, settingsAsked: 0 }
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    if (!server.up) throw new TypeError('Failed to fetch')
    if (path === '/api/dev/settings') {
      server.settingsAsked += 1
      return new Response(JSON.stringify(SETTINGS), { status: 200 })
    }
    return new Response(JSON.stringify({ error: 'not in this test' }), { status: 404 })
  }))
  return server
}

async function wait(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

describe('App', () => {
  it('shows the five tabs once the settings are in, and switches between them', async () => {
    vi.useFakeTimers()
    window.location.hash = ''
    fakeServer()
    render(<App />)
    await wait(0)

    const names = screen.getAllByRole('tab').map(t => t.textContent)
    expect(names).toEqual(['Checks', 'Live', 'System', 'Database', 'Storage'])
    expect(screen.getByRole('tab', { name: 'Checks' })).toHaveAttribute('aria-selected', 'true')

    fireEvent.click(screen.getByRole('tab', { name: 'Database' }))
    expect(screen.getByRole('tab', { name: 'Database' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel', { name: 'Database' })).toBeVisible()
    expect(window.location.hash).toBe('#database')
  })

  it('says when the server can\'t be reached, keeps trying, and gets going once it answers', async () => {
    vi.useFakeTimers()
    const server = fakeServer()
    server.up = false
    render(<App />)
    await wait(0)
    expect(screen.getByText(/Can't reach Desk Talk, trying again/)).toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()

    server.up = true
    await wait(FIRST_TRY_AGAIN_MS)
    expect(screen.queryByText(/Can't reach Desk Talk/)).not.toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(5)
  })

  it('keeps the tabs and shows the bar when the server goes away later', async () => {
    vi.useFakeTimers()
    const server = fakeServer()
    render(<App />)
    await wait(0)
    server.up = false
    await wait(SETTINGS.poll_sec * 1000)
    expect(screen.getByText(/Can't reach Desk Talk, trying again/)).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(5)
  })
})
