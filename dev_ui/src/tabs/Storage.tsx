/// <reference types="vite/client" />
// The Storage tab: what this browser keeps for the app page, what's in the data folder on this
// computer, and what the Pi says about its own card. Everything here is read-only, except deleting
// one localStorage key after you say yes.
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { getFiles, getHealth, getPreview, type DataFile, type DevSettings } from '../api'
import {
  deleteBrowserKey, deleteQuestion, readBrowserStorage, whereThisPageIs, type BrowserKey,
} from '../browserStorage'
import { formatAgo, formatBytes, formatDateTime } from '../format'
import { ErrorLine, Pill, Table } from '../ui'
import { usePoll } from '../usePoll'

const MB = 1024 * 1024

export default function Storage(props: { settings: DevSettings }): JSX.Element {
  return (
    <div className="stack">
      <InThisBrowser webPort={props.settings.web_port} />
      <OnThisComputer settings={props.settings} />
      <OnThePi settings={props.settings} />
    </div>
  )
}

// ── in this browser ──

/** window.localStorage, or null when the browser won't give it (reading it can throw). */
function browserStore(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function readAll(): BrowserKey[] {
  const store = browserStore()
  return store ? readBrowserStorage(store) : []
}

function InThisBrowser(props: { webPort: number }): JSX.Element {
  const [rows, setRows] = useState<BrowserKey[]>(readAll)
  const [picked, setPicked] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reread = useCallback(() => setRows(readAll()), [])

  // The app page open in another tab fires 'storage' here whenever it saves.
  useEffect(() => {
    window.addEventListener('storage', reread)
    return () => window.removeEventListener('storage', reread)
  }, [reread])

  const remove = (row: BrowserKey) => {
    if (!window.confirm(deleteQuestion(row))) return
    const store = browserStore()
    setError(store ? deleteBrowserKey(store, row.key) : "This browser won't let the page use its storage.")
    if (picked === row.key) setPicked(null)
    reread()
  }

  // npm run dev sends a development build of the page, so this tells us where the page came from.
  const where = whereThisPageIs(window.location, import.meta.env.DEV, props.webPort)
  const shown = rows.find(r => r.key === picked)

  return (
    <div className="card">
      <div className="row">
        <h2>In this browser</h2>
        <button type="button" className="btn" onClick={reread}>Read again</button>
      </div>
      <p className={where.sharesWithApp ? 'muted small' : 'error'}>{where.words}</p>
      <p className="muted small">The phone page keeps its own storage on the phone, so it isn't here.</p>
      <ErrorLine error={error} />
      <Table
        rows={rows}
        rowKey={r => r.key}
        level={r => (r.key === picked ? 'ok' : undefined)}
        onRowClick={r => setPicked(r.key === picked ? null : r.key)}
        empty="Nothing is stored in this browser for this address."
        columns={[
          { label: 'Key', cell: r => <code>{r.key}</code> },
          { label: 'Used by', cell: r => r.usedBy },
          { label: 'Size', cell: r => formatBytes(r.bytes), numeric: true },
          {
            label: '',
            cell: r => (
              <button type="button" className="btn danger" aria-label={`Delete ${r.key}`}
                      onClick={e => { e.stopPropagation(); remove(r) }}>
                Delete
              </button>
            ),
          },
        ]}
      />
      {shown && (
        <>
          <h3>{shown.key}</h3>
          <pre className="printout">{shown.json !== null ? JSON.stringify(shown.json, null, 2) : shown.value}</pre>
        </>
      )}
    </div>
  )
}

// ── on this computer ──

export interface TreeRow {
  file: DataFile
  name: string                     // the last part of its path
  depth: number                    // how many folders deep
}

function parentOf(path: string): string {
  const i = path.lastIndexOf('/')
  return i < 0 ? '' : path.slice(0, i)
}

/**
 * The data folder's flat list as tree rows: folders first, then files, each sorted by name, with
 * a folder's contents only when its path is in `open`. Something whose folder isn't in the list
 * hangs off the nearest folder that is.
 */
export function visibleTree(files: DataFile[], open: ReadonlySet<string>): TreeRow[] {
  const folders = new Set(files.filter(f => f.is_folder).map(f => f.path))
  const inside = new Map<string, DataFile[]>()
  for (const f of files) {
    let parent = parentOf(f.path)
    while (parent && !folders.has(parent)) parent = parentOf(parent)
    inside.set(parent, [...(inside.get(parent) ?? []), f])
  }
  const rows: TreeRow[] = []
  const walk = (folder: string, depth: number) => {
    const here = [...(inside.get(folder) ?? [])].sort((a, b) =>
      a.is_folder !== b.is_folder ? (a.is_folder ? -1 : 1) : a.path.localeCompare(b.path))
    for (const file of here) {
      rows.push({ file, name: file.path.slice(folder ? folder.length + 1 : 0), depth })
      if (file.is_folder && open.has(file.path)) walk(file.path, depth + 1)
    }
  }
  walk('', 0)
  return rows
}

const isDatabase = (path: string) => path.toLowerCase().endsWith('.sqlite')

function OnThisComputer(props: { settings: DevSettings }): JSX.Element {
  const files = usePoll(getFiles, props.settings.poll_sec * 1000)
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set())
  const [picked, setPicked] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ text: string | null, error: string | null }>({ text: null, error: null })

  useEffect(() => {
    if (picked === null) return
    let gone = false
    setPreview({ text: null, error: null })
    getPreview(picked).then(
      text => { if (!gone) setPreview({ text, error: null }) },
      (e: unknown) => { if (!gone) setPreview({ text: null, error: e instanceof Error ? e.message : String(e) }) },
    )
    return () => { gone = true }
  }, [picked])

  const click = (row: TreeRow) => {
    const { path, is_folder } = row.file
    if (is_folder) {
      setOpen(old => {
        const next = new Set(old)
        if (next.has(path)) next.delete(path)
        else next.add(path)
        return next
      })
    } else if (!isDatabase(path)) {
      setPicked(path === picked ? null : path)
    }
  }

  const nameCell = (row: TreeRow): ReactNode => {
    const { path, is_folder } = row.file
    const name = is_folder
      ? <strong>{row.name}/</strong>
      : isDatabase(path)
        ? <a href="#database" onClick={e => e.stopPropagation()}>{row.name}</a>
        : <span>{row.name}</span>
    return <span className="mono" style={{ paddingLeft: `${row.depth * 1.5}em` }}>{name}</span>
  }

  return (
    <div className="card">
      <h2>On this computer</h2>
      <p className="muted small">
        The data folder, <code>{props.settings.data_folder}</code>. Click a folder to open it, a file to see its
        start. Databases open in the Database tab.
      </p>
      <ErrorLine error={files.error} />
      {files.data === null
        ? !files.error && <p className="muted">Asking for the data folder…</p>
        : (
          <Table
            rows={visibleTree(files.data, open)}
            rowKey={r => r.file.path}
            level={r => (r.file.path === picked ? 'ok' : undefined)}
            onRowClick={click}
            empty="The data folder is empty."
            columns={[
              { label: 'Name', cell: nameCell },
              { label: 'Size', cell: r => formatBytes(r.file.bytes), numeric: true },
              { label: 'Changed', cell: r => formatDateTime(r.file.modified) },
            ]}
          />
        )}
      {picked !== null && (
        <>
          <h3>{picked}</h3>
          <ErrorLine error={preview.error} />
          {preview.text === null
            ? !preview.error && <p className="muted">Reading…</p>
            : <pre className="printout">{preview.text}</pre>}
        </>
      )}
    </div>
  )
}

// ── on the Pi ──

function Stat(props: { label: string, children: ReactNode }): JSX.Element {
  return (
    <div className="card">
      <div className="muted small">{props.label}</div>
      <strong>{props.children}</strong>
    </div>
  )
}

function OnThePi(props: { settings: DevSettings }): JSX.Element {
  const health = usePoll(getHealth, props.settings.poll_sec * 1000)
  const pi = health.data?.pi
  const notYet = <span className="muted">not sent yet</span>
  const now = Date.now() / 1000

  let body: ReactNode
  if (health.data === null) {
    body = !health.error && <p className="muted">Asking Desk Talk about the Pi…</p>
  } else if (!pi) {
    body = <p className="muted">The camera isn't the Pi, so there's nothing from the Pi here.</p>
  } else {
    const lowDisk = pi.disk_free_mb !== undefined && pi.disk_free_mb < props.settings.health.pi_disk_warn_mb
    body = (
      <div className="cards">
        <Stat label="Photos stored">{pi.photos_stored ?? notYet}</Stat>
        <Stat label="Disk free">
          {pi.disk_free_mb === undefined
            ? notYet
            : lowDisk
              ? <Pill level="warn" title="Less than settings dev.health.pi_disk_warn_mb">{formatBytes(pi.disk_free_mb * MB)}</Pill>
              : formatBytes(pi.disk_free_mb * MB)}
        </Stat>
        <Stat label="Last cleanup">
          {pi.last_cleanup_ts === undefined
            ? notYet
            : <span title={formatDateTime(pi.last_cleanup_ts)}>{formatAgo(pi.last_cleanup_ts, now)}</span>}
        </Stat>
      </div>
    )
  }

  return (
    <div className="card">
      <h2>On the Pi</h2>
      <ErrorLine error={health.error} />
      {body}
    </div>
  )
}
