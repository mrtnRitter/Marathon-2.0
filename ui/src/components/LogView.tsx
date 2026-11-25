import type { LogLine } from '../data/types'
import { format_time } from '../lib/format'
import { log_level_color } from '../theme/status'
import './LogView.css'

export function LogView({ lines, live = false, module_log = false }: { lines: LogLine[]; live?: boolean; module_log?: boolean }) {
  if (module_log) {
    return (
      <div className="log-view-module" role="region" aria-label="Modul-Log" tabIndex={0}>
        <table className="log-module-table">
          <thead>
            <tr><th scope="col">Zeit</th><th scope="col">Stufe</th><th scope="col">Asset</th><th scope="col">Worker</th><th scope="col">Text</th></tr>
          </thead>
          <tbody>
            {lines.map((line, index) => (
              <tr key={index}>
                <td>{format_time(line.ts)}</td>
                <td className="log-level" style={{ color: log_level_color[line.level] }}>{line.level.toUpperCase()}</td>
                <td className="log-context-value">{line.asset || '–'}</td>
                <td className="log-context-value">{line.worker || '–'}</td>
                <td className="log-module-text" style={{ color: line.level === 'error' ? log_level_color.error : undefined }}>{line.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {lines.length === 0 && <div className="log-module-empty">Keine Vorkommnisse</div>}
      </div>
    )
  }

  const show_context = lines.some(line => 'asset' in line || 'worker' in line)
  return (
    <pre className="log-view">
      {lines.length === 0 && <span className="dim">Noch keine Log-Einträge</span>}
      {lines.map((line, index) => (
        <div key={index} className={`log-line${show_context ? ' log-line-context' : ''}`}>
          <span className="log-ts">{format_time(line.ts)}</span>
          <span className="log-level" style={{ color: log_level_color[line.level] }}>{line.level.toUpperCase().padEnd(5)}</span>
          {show_context && <>
            <span className="log-asset">{line.asset ?? '–'}</span>
            <span className="log-worker">{line.worker ?? '–'}</span>
          </>}
          <span style={{ color: line.level === 'error' ? log_level_color.error : undefined }}>{line.text}</span>
        </div>
      ))}
      {live && <div className="log-cursor">▍</div>}
    </pre>
  )
}
