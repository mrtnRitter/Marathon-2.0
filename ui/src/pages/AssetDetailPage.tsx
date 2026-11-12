import { Link, useNavigate, useParams } from 'react-router-dom'
import { KeyValue } from '../components/KeyValue'
import { LogView } from '../components/LogView'
import { PageHeader } from '../components/PageHeader'
import { ReprocessPanel } from '../features/assets/ReprocessPanel'
import { StatusBadge } from '../components/StatusBadge'
import { get_asset_detail, get_workflow } from '../data/api'
import { use_data } from '../data/use_data'
import { useState } from 'react'
import { AssetTimeline } from '../features/assets/AssetTimeline'
import { format_datetime } from '../lib/format'
import { asset_status_meta, message_kind_color, step_status_meta } from '../theme/status'
import './AssetDetailPage.css'

export function AssetDetailPage() {
  const { asset_id = '' } = useParams()
  const navigate = useNavigate()
  const { data: asset, loading, reload } = use_data(() => get_asset_detail(asset_id), [asset_id])
  const [reprocess_open, set_reprocess_open] = useState(false)
  const { data: workflow } = use_data(() => (asset ? get_workflow(asset.workflow_id) : Promise.resolve(null)), [asset?.workflow_id])

  if (!asset) {
    return (
      <>
        <PageHeader title={loading ? '…' : 'Asset nicht gefunden'} breadcrumb={<Link to="/assets">Assets</Link>} />
        {!loading && <div className="page-content"><p className="muted">Kein Asset mit dieser Kennung.</p></div>}
      </>
    )
  }
  return (
    <>
      <PageHeader
        breadcrumb={<><Link to="/assets">Assets</Link> / {workflow && <Link to={`/workflows/${workflow.id}/assets`}>{workflow.name}</Link>}</>}
        title={<>{asset.name}<StatusBadge meta={asset_status_meta[asset.status]} /></>}
        actions={[
          { label: 'Zurück', icon: 'arrow-left', on_click: () => navigate(-1) },
          { label: 'Nach oben', icon: 'arrow-up' },
          { label: 'Erneut verarbeiten', icon: 'reprocess', disabled: asset.status === 'in_progress', on_click: () => set_reprocess_open(true) },
          { label: 'Abbrechen', icon: 'cancel' },
          { label: asset.status === 'on_hold' ? 'Freigeben' : 'Anhalten', icon: asset.status === 'on_hold' ? 'play' : 'pause' },
          { label: 'Entfernen', icon: 'trash' },
        ]}
      />
      <ReprocessPanel open={reprocess_open} asset_ids={[asset.id]} current_workflow_id={asset.workflow_id}
        on_close={() => set_reprocess_open(false)} on_done={ids => {
          set_reprocess_open(false)
          if (ids[0] !== asset.id) navigate(`/assets/${ids[0]}`)
          else reload()
        }} />
      <div className="page-content asset-detail">
        <section className="panel asset-detail-head">
          <h2 className="panel-title">Kopfdaten</h2>
          <div className="panel-body">
            <KeyValue entries={[
              ['Kennung', asset.id],
              ['Workflow', workflow ? <Link to={`/workflows/${workflow.id}/modules`}>{workflow.name}</Link> : asset.workflow_id],
              ['Gesamtstatus', <StatusBadge meta={asset_status_meta[asset.status]} />],
              ['Aktuelles Modul', asset.current_module ?? '–'],
              ['Schrittstatus', asset.step_status ? <StatusBadge meta={step_status_meta[asset.step_status]} /> : '–'],
              ['Angelegt', format_datetime(asset.created_at)],
              ['Letzte Änderung', format_datetime(asset.updated_at)],
            ]} />
          </div>
        </section>
        <section className="panel asset-detail-meta">
          <h2 className="panel-title">Metadaten</h2>
          <div className="panel-body"><KeyValue entries={Object.entries(asset.metadata)} /></div>
        </section>
        <section className="panel asset-detail-timeline">
          <h2 className="panel-title">Zeitstrahl</h2>
          <div className="panel-body"><AssetTimeline entries={asset.timeline} /></div>
        </section>
        <section className="panel asset-detail-messages">
          <h2 className="panel-title">Meldungen</h2>
          <div className="panel-body">
            {asset.messages.map((message, index) => (
              <div key={index} className="asset-message" style={{ borderLeftColor: message_kind_color[message.kind] }}>
                <span className="dim">{format_datetime(message.ts)}</span> {message.text}
              </div>
            ))}
          </div>
        </section>
        <section className="panel asset-detail-log">
          <h2 className="panel-title">Log {asset.step_status === 'running' && <span className="live-dot">live</span>}</h2>
          <div className="panel-body"><LogView lines={asset.log} live={asset.step_status === 'running'} /></div>
        </section>
      </div>
    </>
  )
}
