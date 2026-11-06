import type { StatusMeta } from '../theme/status'
import './StatusBadge.css'

export function StatusBadge({ meta }: { meta: StatusMeta }) {
  return (
    <span className="status-badge" style={{ color: meta.color }}>
      <span className="status-badge-dot" />
      {meta.label}
    </span>
  )
}
