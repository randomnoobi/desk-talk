import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DataFile, DevSettings, PiStatus } from '../api'
import Storage, { visibleTree } from './Storage'

const SETTINGS: DevSettings = {
  poll_sec: 1,
  checks: ['Fast tests'],
  health: { pi_cpu_warn_c: 70, pi_cpu_bad_c: 80, pi_disk_warn_mb: 1000, detect_warn_ms: 500 },
  trace_pictures: 90,
  sql_max_rows: 500,
  data_folder: '/tmp/data',
  disconnect_after_sec: 3,
  web_port: 5000,
  flicker_flips_per_min: 4,
  object_states: { on_confidence: 0.5, off_confidence: 0.3, appear_sec: 2, gone_sec: 5, window_pictures: 5, vote_share: 0.6 },
}

const FILES: DataFile[] = [
  { path: 'object_states.sqlite', bytes: 8192, modified: 1_700_000_000, is_folder: false },
  { path: 'logs', bytes: 3000, modified: 1_700_000_000, is_folder: true },
  { path: 'logs/2026-10-25.jsonl', bytes: 3000, modified: 1_700_000_000, is_folder: false },
  { path: 'notes.txt', bytes: 12, modified: 1_700_000_000, is_folder: false },
]

const PI: Partial<PiStatus> = { last_frame_id: 'a1', photos_stored: 42, disk_free_mb: 2048, last_cleanup_ts: 1_700_000_000 }

function fakeServer(pi: Partial<PiStatus> | null = PI) {
  const asked: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    asked.push(path)
    const answer = (body: unknown) => new Response(JSON.stringify(body), { status: 200 })
    if (path === '/api/dev/files') return answer({ files: FILES })
    if (path.startsWith('/api/dev/files/preview?path=')) {
      const file = decodeURIComponent(path.split('=')[1])
      return answer({ path: file, text: `the start of ${file}` })
    }
    if (path === '/api/health') return answer({ pi })
    return new Response(JSON.stringify({ error: 'not in this test' }), { status: 404 })
  }))
  return asked
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllEnvs()
  localStorage.clear()
})

describe('Storage tab: this browser', () => {
  it('lists each key with what uses it, and shows its value as JSON when clicked', async () => {
    fakeServer()
    localStorage.setItem('desktalk_todo_v1', JSON.stringify({ type: 'todo', items: [] }))
    render(<Storage settings={SETTINGS} />)

    const row = screen.getByText('desktalk_todo_v1').closest('tr')!
    expect(within(row).getByText("Paper's to-do list")).toBeInTheDocument()
    fireEvent.click(within(row).getByText('desktalk_todo_v1'))
    expect(screen.getByText(/"type": "todo"/)).toBeInTheDocument()
  })

  it('asks before deleting, and only deletes after a yes', async () => {
    fakeServer()
    localStorage.setItem('desktalk_todo_v1', '{}')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<Storage settings={SETTINGS} />)

    fireEvent.click(screen.getByRole('button', { name: 'Delete desktalk_todo_v1' }))
    expect(confirm).toHaveBeenCalledWith("Delete desktalk_todo_v1? The app page loses Paper's to-do list.")
    expect(localStorage.getItem('desktalk_todo_v1')).toBe('{}')
    expect(screen.getByText('desktalk_todo_v1')).toBeInTheDocument()

    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Delete desktalk_todo_v1' }))
    expect(localStorage.getItem('desktalk_todo_v1')).toBeNull()
    expect(screen.queryByText('desktalk_todo_v1')).not.toBeInTheDocument()
  })

  it('says it shares the app page\'s storage when the server sent it', () => {
    fakeServer()
    vi.stubEnv('DEV', false)
    render(<Storage settings={SETTINGS} />)
    expect(screen.getByText(/share this storage/)).toBeInTheDocument()
    expect(screen.getByText(/phone/)).toBeInTheDocument()
  })

  it('says where to open it when npm run dev sent it, using settings → web.port', () => {
    fakeServer()
    vi.stubEnv('DEV', true)
    render(<Storage settings={{ ...SETTINGS, web_port: 5123 }} />)
    expect(screen.getByText(/:5123\/dev\//)).toBeInTheDocument()
    expect(screen.queryByText(/<web\.port>/)).not.toBeInTheDocument()
  })
})

describe('Storage tab: this computer', () => {
  it('shows the data folder as a tree, opens folders, previews files and links databases', async () => {
    const asked = fakeServer()
    render(<Storage settings={SETTINGS} />)

    await screen.findByText('notes.txt')
    expect(screen.queryByText('2026-10-25.jsonl')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('logs/'))
    expect(screen.getByText('2026-10-25.jsonl')).toBeInTheDocument()

    expect(screen.getByRole('link', { name: 'object_states.sqlite' })).toHaveAttribute('href', '#database')

    fireEvent.click(screen.getByText('notes.txt'))
    expect(await screen.findByText('the start of notes.txt')).toBeInTheDocument()
    expect(asked).toContain('/api/dev/files/preview?path=notes.txt')
  })

  it('puts folders first and indents what is inside', () => {
    const rows = visibleTree(FILES, new Set(['logs']))
    expect(rows.map(r => [r.name, r.depth])).toEqual([
      ['logs', 0], ['2026-10-25.jsonl', 1], ['notes.txt', 0], ['object_states.sqlite', 0],
    ])
    expect(visibleTree(FILES, new Set()).map(r => r.name)).toEqual(['logs', 'notes.txt', 'object_states.sqlite'])
  })
})

describe('Storage tab: the Pi', () => {
  it('shows photos stored, disk free and the last cleanup', async () => {
    fakeServer()
    render(<Storage settings={SETTINGS} />)
    expect(await screen.findByText('42')).toBeInTheDocument()
    expect(screen.getByText('2 GB')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/ago/)).toBeInTheDocument())
  })

  it('says so when the camera is not the Pi', async () => {
    fakeServer(null)
    render(<Storage settings={SETTINGS} />)
    expect(await screen.findByText(/camera isn't the Pi/)).toBeInTheDocument()
  })
})
