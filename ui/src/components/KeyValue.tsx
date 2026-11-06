import type { ReactNode } from 'react'
import './KeyValue.css'

export function KeyValue({ entries }: { entries: [string, ReactNode][] }) {
  return (
    <dl className="key-value">
      {entries.map(([key, value]) => (
        <div key={key} className="key-value-row"><dt>{key}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  )
}
