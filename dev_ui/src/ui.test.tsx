import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ErrorLine, Pill, Table } from './ui'

describe('Table', () => {
  const rows = [{ name: 'events', rows: 3 }, { name: 'snapshots', rows: 0 }]
  const columns = [{ label: 'Table', cell: (r: typeof rows[0]) => r.name },
                   { label: 'Rows', cell: (r: typeof rows[0]) => r.rows, numeric: true }]

  it('shows a row per item, coloured by its level, and says when a row is clicked', () => {
    const picked = vi.fn()
    render(<Table rows={rows} columns={columns} rowKey={r => r.name}
                  level={r => (r.rows ? 'ok' : 'off')} onRowClick={picked} />)
    expect(screen.getAllByRole('row')).toHaveLength(3)
    const events = screen.getByText('events').closest('tr')
    expect(events).toHaveClass('ok', 'clickable')
    fireEvent.click(screen.getByText('snapshots'))
    expect(picked).toHaveBeenCalledWith(rows[1])
  })

  it('shows the empty words when there are no rows', () => {
    render(<Table rows={[]} columns={columns} rowKey={r => r.name} empty="No tables yet" />)
    expect(screen.getByText('No tables yet')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

describe('Pill and ErrorLine', () => {
  it('colour by level, and show nothing without an error', () => {
    const { container } = render(<><Pill level="bad">82 °C</Pill><ErrorLine error={null} /></>)
    expect(screen.getByText('82 °C')).toHaveClass('pill', 'bad')
    expect(container.querySelector('.error')).toBeNull()
  })
})
