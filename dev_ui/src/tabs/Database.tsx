// The Database tab: the databases and their tables on the left, a query box and its answer on the right.
// The server opens every database read-only, so nothing typed here can change one. Counting the rows
// reads every table, so it happens on open, every settings.table_counts_every_sec and on "Count again".
import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { getDatabases, getTables, runQuery, type DevSettings, type QueryResult, type TableInfo } from '../api'
import { OBJECT_STATES_DB, readyMade } from '../queries'
import { ErrorLine, Table, type Column } from '../ui'
import { usePoll } from '../usePoll'

interface DatabaseTables {
  name: string
  tables: TableInfo[]
}

async function loadEverything(): Promise<DatabaseTables[]> {
  const names = await getDatabases()
  return Promise.all(names.map(async name => ({ name, tables: await getTables(name) })))
}

/** A table's name as SQL: as it is when it's a plain word, in double quotes otherwise. */
function sqlName(name: string): string {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : `"${name.replace(/"/g, '""')}"`
}

/** Newest rows first. No LIMIT: the server stops at settings.sql_max_rows and says so. */
export function selectAll(table: string): string {
  return `SELECT * FROM ${sqlName(table)} ORDER BY rowid DESC`
}

function cellOf(value: unknown): ReactNode {
  if (value === null || value === undefined) return <span className="muted">NULL</span>
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** The result's columns for <Table>. Two columns with one name get "(2)", "(3)"... so each label is different. */
function resultColumns(names: string[]): Column<unknown[]>[] {
  const seen = new Map<string, number>()
  return names.map((name, i) => {
    const times = (seen.get(name) ?? 0) + 1
    seen.set(name, times)
    return {
      label: times === 1 ? name : `${name} (${times})`,
      cell: (row: unknown[]) => cellOf(row[i]),
      numeric: false,
    }
  })
}

interface Answer {
  result: QueryResult | null
  error: string | null
  busy: boolean
}

export default function Database(props: { settings: DevSettings }): JSX.Element {
  const { settings } = props
  const list = usePoll(loadEverything, settings.table_counts_every_sec * 1000)
  const queries = useMemo(() => readyMade(settings), [settings])
  const databases = list.data ?? []

  const [picked, setPicked] = useState<string | null>(null)
  const [table, setTable] = useState<string | null>(null)
  const [sql, setSql] = useState('')
  const [answer, setAnswer] = useState<Answer>({ result: null, error: null, busy: false })
  const asked = useRef(0)                    // only the newest query's answer is shown

  const names = databases.map(d => d.name)
  const db = picked ?? (names.includes(OBJECT_STATES_DB) ? OBJECT_STATES_DB : names[0] ?? null)
  const chosen = databases.find(d => d.name === db)?.tables.find(t => t.name === table) ?? null

  const run = async (onDb: string | null, text: string) => {
    if (onDb === null || !text.trim()) return
    const mine = ++asked.current
    setAnswer(old => ({ ...old, busy: true }))
    try {
      const result = await runQuery(onDb, text)
      if (mine === asked.current) setAnswer({ result, error: null, busy: false })
    } catch (e) {
      if (mine === asked.current) setAnswer({ result: null, error: e instanceof Error ? e.message : String(e), busy: false })
    }
  }

  const fillAndRun = (onDb: string, text: string, tableName: string | null) => {
    setPicked(onDb)
    setTable(tableName)
    setSql(text)
    void run(onDb, text)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      void run(db, sql)
    }
  }

  const { result } = answer
  return (
    <div className="split">
      <div className="card stack">
        <div className="row">
          <h2 style={{ marginBottom: 0 }}>Databases</h2>
          <button type="button" className="btn" onClick={list.refresh}>Count again</button>
        </div>
        <ErrorLine error={list.error} />
        {list.data === null && !list.error && <p className="muted">Asking for the tables…</p>}
        {list.data !== null && databases.length === 0 && <p className="muted">No databases.</p>}
        {databases.map(d => (
          <div key={d.name}>
            <h3 className="mono">{d.name}</h3>
            <Table
              rows={d.tables}
              rowKey={t => t.name}
              columns={[{ label: 'Table', cell: t => t.name }, { label: 'Rows', cell: t => t.rows, numeric: true }]}
              level={t => (d.name === db && t.name === table ? 'ok' : undefined)}
              onRowClick={t => fillAndRun(d.name, selectAll(t.name), t.name)}
              empty="No tables yet."
            />
          </div>
        ))}
      </div>

      <div className="card stack">
        <h2>Query</h2>
        {chosen && (
          <div className="table-wrap">
            <pre className="mono small muted">{chosen.sql}</pre>
          </div>
        )}
        {names.includes(OBJECT_STATES_DB) && (
          <div className="row">
            <span className="muted small">Ready-made:</span>
            {queries.map(q => (
              <button key={q.name} type="button" className="btn" onClick={() => fillAndRun(OBJECT_STATES_DB, q.sql, null)}>
                {q.name}
              </button>
            ))}
          </div>
        )}
        <textarea className="code" aria-label="Query" spellCheck={false} value={sql}
                  placeholder="SELECT … (Ctrl+Enter or Cmd+Enter runs it)"
                  onChange={e => setSql(e.target.value)} onKeyDown={onKeyDown} />
        <div className="row">
          <button type="button" className="btn primary" disabled={db === null || !sql.trim()} onClick={() => void run(db, sql)}>
            Run
          </button>
          {names.length > 1 && (
            <label className="row small">
              on
              <select className="input" value={db ?? ''} onChange={e => { setPicked(e.target.value); setTable(null) }}>
                {names.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          )}
          {names.length === 1 && <span className="muted small">on <span className="mono">{db}</span></span>}
          {answer.busy && <span className="muted small">Running…</span>}
        </div>
        <p className="muted small">Read-only: this page can't change the database.</p>

        <ErrorLine error={answer.error} />
        {result && (
          <section aria-label="Query result" className="stack">
            <p className="muted small">
              {result.rows.length} {result.rows.length === 1 ? 'row' : 'rows'} in {Number(result.took_ms.toFixed(1))} ms
              {result.truncated && `, showing the first ${settings.sql_max_rows} rows`}
            </p>
            <Table rows={result.rows} rowKey={(_, i) => String(i)} columns={resultColumns(result.columns)} empty="No rows." />
          </section>
        )}
      </div>
    </div>
  )
}
