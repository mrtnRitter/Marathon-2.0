import type { AssetStatus } from '../data/types'
import { asset_status_meta, asset_statuses } from '../theme/status'
import './StatusBar.css'

/** Stacked bar showing the share of assets per overall status. */
export function StatusBar({ counts }: { counts: Record<AssetStatus, number> }) {
  const total = asset_statuses.reduce((sum, status) => sum + counts[status], 0)
  return (
    <div className="status-bar" title={asset_statuses.map(status => `${asset_status_meta[status].label}: ${counts[status]}`).join(' · ')}>
      {total > 0 && asset_statuses.map(status => counts[status] > 0 && (
        <span key={status} style={{ flexGrow: counts[status], background: asset_status_meta[status].color }} />
      ))}
    </div>
  )
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <span className="progress-bar"><span style={{ width: `${value}%` }} /><em>{value}%</em></span>
  )
}
