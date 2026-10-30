import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getDatabases, getTables, runQuery, type DevSettings, type QueryResult, type TableInfo } from '../api'
import { readyMade } from '../queries'
import Database from './Database'

// The page's calls to the server are faked; ApiError and the rest of api.ts stay real.
vi.mock('../api', async importOriginal => ({
  ...await importOriginal<typeof import('../api')>(),
  getDatabases: vi.fn(),
  getTables: vi.fn(),
  runQuery: vi.fn(),
}))

const SETTINGS: DevSettings = {
  poll_sec: 1,
  checks: ['Fast tests'],
  health: { pi_cpu_warn_c: 70, pi_cpu_bad_c: 80, pi_disk_warn_mb: 1000, detect_warn_ms: 500 },
  trace_pictures: 90,
  sql_max_rows: 7,                 // an odd number, so the test knows it came from here
  data_folder: '/tmp/data',
  disconnect_after_sec: 3,
  web_port: 5000,
  flicker_flips_per_min: 4,
  output_keep_kb: 2000,
  flips_window_min: 10,
  table_counts_every_sec: 30,
  trace_events_kept: 50,
  trace_photos_kept: 6,
  trace_step_photos_kept: 24,
  object_states: { on_confidence: 0.5, off_confidence: 0.3, appear_sec: 2, gone_sec: 5, window_pictures: 5, vote_share: 0.6 },
}

const EVENTS: TableInfo = {
  name: 'events',
  columns: ['id', 'ts', 'object', 'type'],
  rows: 3,
  // Like the real SqliteBrowser: the table, then its indexes and triggers.
  sql: [
    'CREATE TABLE events (\n    id INTEGER PRIMARY KEY,\n    ts REAL NOT NULL\n);',
    'CREATE INDEX events_ts ON events (ts);',
    "CREATE TRIGGER events_no_delete BEFORE DELETE ON events\nBEGIN SELECT RAISE(ABORT, 'events is append-only'); END;",
  ].join('\n'),
}

function result(over: Partial<QueryResult> = {}): QueryResult {
  return { columns: ['id', 'object'], rows: [[3, 'cup'], [2, null]], truncated: false, took_ms: 4, ...over }
}

beforeEach(() => {
  vi.mocked(getTables).mockClear()
  vi.mocked(getDatabases).mockResolvedValue(['object_states'])
  vi.mocked(getTables).mockResolvedValue([EVENTS])
  vi.mocked(runQuery).mockResolvedValue(result())
})

describe('Database tab', () => {
  it('lists the tables, and clicking events runs SELECT * on it and shows the rows', async () => {
    render(<Database settings={SETTINGS} />)
    fireEvent.click(await screen.findByText('events'))

    expect(runQuery).toHaveBeenCalledWith('object_states', 'SELECT * FROM events ORDER BY rowid DESC')
    expect(screen.getByLabelText('Query')).toHaveValue('SELECT * FROM events ORDER BY rowid DESC')
    const made = screen.getByText(/CREATE TABLE events/)                // how it's made, line breaks kept
    expect(made.tagName).toBe('PRE')
    expect(made.textContent).toBe(EVENTS.sql)
    const rows = await screen.findByRole('region', { name: 'Query result' })
    expect(within(rows).getByText('cup')).toBeInTheDocument()
    expect(within(rows).getByText('NULL')).toBeInTheDocument()
    expect(screen.getByText(/2 rows in 4 ms/)).toBeInTheDocument()
    expect(screen.getByText(/Read-only: this page can't change the database/)).toBeInTheDocument()
  })

  it('shows the reason in red when the server refuses a query', async () => {
    vi.mocked(runQuery).mockRejectedValue(new ApiError(400, 'Only one read-only statement can run.'))
    render(<Database settings={SETTINGS} />)
    await screen.findByText('events')
    fireEvent.change(screen.getByLabelText('Query'), { target: { value: 'DELETE FROM events' } })
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Only one read-only statement can run.')
    expect(runQuery).toHaveBeenCalledWith('object_states', 'DELETE FROM events')
    expect(screen.queryByRole('region', { name: 'Query result' })).not.toBeInTheDocument()
  })

  it('says only the first sql_max_rows rows are shown when the result was cut short', async () => {
    vi.mocked(runQuery).mockResolvedValue(result({ truncated: true }))
    render(<Database settings={SETTINGS} />)
    fireEvent.click(await screen.findByText('events'))

    expect(await screen.findByText(/showing the first 7 rows/)).toBeInTheDocument()
  })

  it('runs the box with Ctrl+Enter or Cmd+Enter', async () => {
    render(<Database settings={SETTINGS} />)
    await screen.findByText('events')
    const box = screen.getByLabelText('Query')
    fireEvent.change(box, { target: { value: 'SELECT 1' } })
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true })
    fireEvent.keyDown(box, { key: 'Enter', metaKey: true })
    fireEvent.keyDown(box, { key: 'Enter' })                  // a plain Enter is a new line

    await screen.findByRole('region', { name: 'Query result' })
    expect(runQuery).toHaveBeenCalledTimes(2)
    expect(runQuery).toHaveBeenLastCalledWith('object_states', 'SELECT 1')
  })

  it('a ready-made query fills the box and runs on the object states database', async () => {
    vi.mocked(getDatabases).mockResolvedValue(['other', 'object_states'])
    render(<Database settings={SETTINGS} />)
    await screen.findAllByText('events')
    const [first] = readyMade(SETTINGS)
    fireEvent.click(screen.getByRole('button', { name: first.name }))

    expect(screen.getByLabelText('Query')).toHaveValue(first.sql)
    expect(runQuery).toHaveBeenCalledWith('object_states', first.sql)
  })

  it('shows why the tables could not be listed', async () => {
    vi.mocked(getDatabases).mockRejectedValue(new ApiError(0, 'no answer from the Desk Talk server'))
    render(<Database settings={SETTINGS} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('no answer from the Desk Talk server')
  })
})

describe('Database tab: counting the rows', () => {
  async function wait(ms: number) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
  }

  afterEach(() => { vi.useRealTimers() })

  it('counts once on open, then every settings.table_counts_every_sec (not every poll_sec), and on Count again', async () => {
    vi.useFakeTimers()
    render(<Database settings={{ ...SETTINGS, poll_sec: 1, table_counts_every_sec: 30 }} />)
    await wait(0)
    expect(getTables).toHaveBeenCalledTimes(1)

    await wait(29_000)
    expect(getTables).toHaveBeenCalledTimes(1)
    await wait(1_000)
    expect(getTables).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByRole('button', { name: 'Count again' }))
    await wait(0)
    expect(getTables).toHaveBeenCalledTimes(3)
  })
})

describe('the ready-made queries', () => {
  it('the flips query looks back settings.flips_window_min and says so', () => {
    const flips = readyMade({ ...SETTINGS, flips_window_min: 7 }).find(q => q.name.startsWith('Flips'))!
    expect(flips.name).toBe('Flips per object, last 7 minutes')
    expect(flips.sql).toContain("- 7 * 60")
    expect(flips.sql).not.toContain('10 * 60')
  })

  it('says "minute" for one', () => {
    expect(readyMade({ ...SETTINGS, flips_window_min: 1 }).map(q => q.name)).toContain('Flips per object, last minute')
  })

  // The server's SqliteBrowser only lets plain reads run: a PRAGMA or anything else is refused.
  it.each(readyMade(SETTINGS).map(q => [q.name, q.sql]))('%s is a plain read', (_, sql) => {
    expect(sql).toMatch(/^\s*(SELECT|WITH)\b/i)
    expect(sql).not.toMatch(/PRAGMA|ATTACH|INSERT|UPDATE|DELETE|;/i)
  })
})
