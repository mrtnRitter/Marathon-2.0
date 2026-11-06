import { useMemo, useState, type ReactNode } from 'react'
import './DataTable.css'

export interface Column<T> {
  key: string
  label: string
  render?: (row: T) => ReactNode
  sort_value?: (row: T) => string | number
  width?: string
  align?: 'left' | 'right'
}

interface Props<T> {
  columns: Column<T>[]
  rows: T[]
  row_key: (row: T) => string
  selected?: string[]
  on_selection_change?: (keys: string[]) => void
  on_row_open?: (row: T) => void
  empty_text?: string
}

/** Compact table with optional checkbox selection, column sorting and double-click to open. */
export function DataTable<T>({ columns, rows, row_key, selected, on_selection_change, on_row_open, empty_text = 'Keine Einträge' }: Props<T>) {
  const [sort, set_sort] = useState<{ key: string; direction: 1 | -1 } | null>(null)
  const selectable = Boolean(on_selection_change)
  const selected_set = new Set(selected)

  const sorted_rows = useMemo(() => {
    const column = columns.find(item => item.key === sort?.key)
    if (!sort || !column) return rows
    const value = column.sort_value ?? ((row: T) => String((row as Record<string, unknown>)[column.key] ?? ''))
    return [...rows].sort((a, b) => (value(a) > value(b) ? 1 : value(a) < value(b) ? -1 : 0) * sort.direction)
  }, [rows, columns, sort])

  const toggle_sort = (key: string) =>
    set_sort(current => (current?.key === key ? (current.direction === 1 ? { key, direction: -1 } : null) : { key, direction: 1 }))

  const toggle_row = (key: string) =>
    on_selection_change?.(selected_set.has(key) ? [...selected_set].filter(item => item !== key) : [...selected_set, key])

  const all_selected = rows.length > 0 && rows.every(row => selected_set.has(row_key(row)))

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {selectable && (
              <th className="data-table-check">
                <input type="checkbox" checked={all_selected} onChange={() => on_selection_change?.(all_selected ? [] : rows.map(row_key))} />
              </th>
            )}
            {columns.map(column => (
              <th key={column.key} style={{ width: column.width, textAlign: column.align }} onClick={() => toggle_sort(column.key)}>
                {column.label}
                <span className="data-table-sort">{sort?.key === column.key ? (sort.direction === 1 ? '▲' : '▼') : ''}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted_rows.map(row => {
            const key = row_key(row)
            return (
              <tr key={key} className={selected_set.has(key) ? 'selected' : undefined}
                onClick={() => selectable && on_selection_change?.([key])} onDoubleClick={() => on_row_open?.(row)}>
                {selectable && (
                  <td className="data-table-check" onClick={event => { event.stopPropagation(); toggle_row(key) }}>
                    <input type="checkbox" checked={selected_set.has(key)} readOnly />
                  </td>
                )}
                {columns.map(column => (
                  <td key={column.key} style={{ textAlign: column.align }}>
                    {column.render ? column.render(row) : String((row as Record<string, unknown>)[column.key] ?? '–')}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
      {rows.length === 0 && <div className="data-table-empty">{empty_text}</div>}
    </div>
  )
}
