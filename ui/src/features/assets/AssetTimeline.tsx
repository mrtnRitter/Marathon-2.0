import type { TimelineEntry } from '../../data/types'
import { format_datetime, format_duration } from '../../lib/format'
import { job_status_meta } from '../../theme/status'
import { StatusBadge } from '../../components/StatusBadge'
import './AssetTimeline.css'

export function AssetTimeline({ entries }: { entries: TimelineEntry[] }) {
  if (!entries.length) return <p className="dim">Noch kein Modul durchlaufen.</p>
  return (
    <ol className="timeline">
      {entries.map(entry => (
        <li key={entry.job_id} className="timeline-entry" style={{ ['--entry-color' as string]: job_status_meta[entry.status].color }}>
          <div className="timeline-head">
            <strong>{entry.module_name}</strong>
            <StatusBadge meta={job_status_meta[entry.status]} />
            {entry.output_label && <span className="chip">Ausgang: {entry.output_label}</span>}
          </div>
          <div className="timeline-meta dim">
            <span>{entry.job_id}</span>
            <span>Versuche {entry.attempts}</span>
            <span>Worker {entry.worker}</span>
            <span>Start {format_datetime(entry.started_at)}</span>
            <span>Dauer {format_duration(entry.duration_s)}</span>
          </div>
        </li>
      ))}
    </ol>
  )
}
