import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DataTable, type Column } from '../../components/DataTable'
import { StatusBadge } from '../../components/StatusBadge'
import { StatusTabs } from '../../components/StatusTabs'
import { Toolbar } from '../../components/Toolbar'
import { get_assets } from '../../data/api'
import type { Asset, AssetStatus, Workflow } from '../../data/types'
import { use_data } from '../../data/use_data'
import { format_datetime, format_relative } from '../../lib/format'
import { asset_status_meta, asset_statuses, step_status_meta } from '../../theme/status'
import './AssetList.css'

interface Props {
  workflow_id?: string
  /** Status tab shown first; defaults to pending. */
  initial_status?: AssetStatus
  /** When given, a workflow column is shown (global asset view). */
  workflows?: Workflow[]
}

/** Asset list with status tiles and actions; shared by the global view and the workflow tab. */
export function AssetList({ workflow_id, workflows, initial_status }: Props) {
  const navigate = useNavigate()
  const { data: assets = [], loading } = use_data(() => get_assets({ workflow_id }), [workflow_id])
  const [status, set_status] = useState<AssetStatus>(initial_status ?? 'pending')
  const [selected, set_selected] = useState<string[]>([])
  const [search, set_search] = useState('')

  const visible = useMemo(() => assets.filter(asset =>
    asset.status === status && asset.name.toLowerCase().includes(search.trim().toLowerCase())), [assets, status, search])

  const workflow_name = (id: string) => workflows?.find(workflow => workflow.id === id)?.name ?? id
  const open = (id: string) => navigate(`/assets/${id}`)
  const columns: Column<Asset>[] = [
    { key: 'name', label: 'Name', render: row => <strong>{row.name}</strong> },
    ...(workflows ? [{ key: 'workflow_id', label: 'Workflow', render: (row: Asset) => workflow_name(row.workflow_id) }] : []),
    { key: 'status', label: 'Gesamtstatus', render: row => <StatusBadge meta={asset_status_meta[row.status]} /> },
    { key: 'current_module', label: 'Aktuelles Modul', render: row => row.current_module ?? <span className="dim">–</span> },
    { key: 'step_status', label: 'Schrittstatus', render: row => (row.step_status ? <StatusBadge meta={step_status_meta[row.step_status]} /> : <span className="dim">–</span>) },
    { key: 'updated_at', label: 'Letzte Änderung', render: row => <span title={format_datetime(row.updated_at)}>{format_relative(row.updated_at)}</span>, sort_value: row => row.updated_at },
  ]
  const none = selected.length === 0

  return (
    <div className="asset-list">
      <StatusTabs active={status} on_change={id => { set_status(id as AssetStatus); set_selected([]) }}
        items={asset_statuses.map(item => ({ id: item, ...asset_status_meta[item], count: assets.filter(asset => asset.status === item).length }))} />
      <div className="asset-list-bar">
        <Toolbar actions={[
          { label: 'Ansehen', icon: 'eye', disabled: selected.length !== 1, on_click: () => open(selected[0]) },
          { label: 'Nach oben', icon: 'arrow-up', disabled: none },
          { label: 'Erneut verarbeiten', icon: 'reprocess', disabled: none },
          { label: 'Abbrechen', icon: 'cancel', disabled: none },
          { label: status === 'on_hold' ? 'Freigeben' : 'Anhalten', icon: status === 'on_hold' ? 'play' : 'pause', disabled: none },
          { label: 'Entfernen', icon: 'trash', disabled: none },
          { label: 'Hinzufügen', icon: 'plus' },
          { label: 'Import', icon: 'upload' },
        ]} />
        <input className="input asset-list-search" placeholder="Suchen …" value={search} onChange={event => set_search(event.target.value)} />
      </div>
      {loading && !assets.length ? <div className="loading">Lade</div> : (
        <DataTable columns={columns} rows={visible} row_key={row => row.id} selected={selected}
          on_selection_change={set_selected} on_row_open={row => open(row.id)}
          empty_text={`Keine Assets mit Status „${asset_status_meta[status].label}“`} />
      )}
    </div>
  )
}
