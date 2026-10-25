// Small pieces every tab can use, so the tabs look alike. The matching CSS is in style.css.
import type { ReactNode } from 'react'

/** How a number is doing: green, amber, red, or grey for "not in use". */
export type Level = 'ok' | 'warn' | 'bad' | 'off'

/** A rounded label coloured by its level, e.g. <Pill level="ok">PRESENT</Pill>. */
export function Pill(props: { level?: Level, children: ReactNode, title?: string }): JSX.Element {
  return <span className={`pill ${props.level ?? ''}`} title={props.title}>{props.children}</span>
}

/** A red line saying what went wrong (an ApiError's message, say). Shows nothing for null. */
export function ErrorLine(props: { error: string | null | undefined }): JSX.Element | null {
  return props.error ? <p className="error" role="alert">{props.error}</p> : null
}

export interface Column<T> {
  label: string
  cell: (row: T) => ReactNode
  numeric?: boolean                // right-aligned, in even-width digits
}

/**
 * A plain table. `level` colours a row's left edge (ok, warn, bad, off).
 * Example: <Table rows={files} rowKey={f => f.path} columns={[{label: 'File', cell: f => f.path}]} />
 */
export function Table<T>(props: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string
  level?: (row: T) => Level | undefined
  onRowClick?: (row: T) => void
  empty?: ReactNode                // shown when there are no rows
}): JSX.Element {
  const { columns, rows, rowKey, level, onRowClick, empty } = props
  if (rows.length === 0 && empty !== undefined) return <p className="muted">{empty}</p>
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>{columns.map(c => <th key={c.label} className={c.numeric ? 'num' : undefined}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)} className={[level?.(row), onRowClick ? 'clickable' : ''].join(' ').trim() || undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}>
              {columns.map(c => <td key={c.label} className={c.numeric ? 'num' : undefined}>{c.cell(row)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
