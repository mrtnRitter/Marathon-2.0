import type { LogLine } from '../data/types'
import { format_time } from '../lib/format'
import { log_level_color } from '../theme/status'
import './LogView.css'

export function LogView({ lines, live = false }: { lines: LogLine[]; live?: boolean }) {
  return (
    <pre className="log-view">
      {lines.length === 0 && <span className="dim">Noch keine Log-Einträge</span>}
      {lines.map((line, index) => (
        <div key={index} className="log-line">
          <span className="log-ts">{format_time(line.ts)}</span>
          <span className="log-level" style={{ color: log_level_color[line.level] }}>{line.level.toUpperCase().padEnd(5)}</span>
          <span style={{ color: line.level === 'error' ? log_level_color.error : undefined }}>{line.text}</span>
        </div>
      ))}
      {live && <div className="log-cursor">▍</div>}
    </pre>
  )
}
