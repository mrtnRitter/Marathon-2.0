import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DataTable, type Column } from '../../components/DataTable'
import { StatusBadge } from '../../components/StatusBadge'
import { StatusTabs } from '../../components/StatusTabs'
import { Toolbar } from '../../components/Toolbar'
import { ReprocessPanel } from './ReprocessPanel'
import { get_assets } from '../../data/api'
import type { Access, Asset, Workflow } from '../../data/types'
import { use_data } from '../../data/use_data'
import { filter_assets, type AssetFilter } from '../../lib/asset_filter'
import { has_access } from '../../lib/access'
import { format_datetime, format_relative } from '../../lib/format'
import { asset_status_meta, asset_statuses, step_status_meta } from '../../theme/status'
import './AssetList.css'

interface Props {
  workflow_id?: string
  workflow_access?: Access
  /** Status tab shown first; defaults to pending. */
  initial_status?: AssetFilter
  /** When given, a workflow column is shown (global asset view). */
  workflows?: Workflow[]
}

/** Asset list with status tiles and actions; shared by the global view and the workflow tab. */
export function AssetList({ workflow_id, workflows, initial_status, workflow_access: current_workflow_access }: Props) {
  const navigate = useNavigate()
  const { data: assets = [], loading, reload } = use_data(() => get_assets({ workflow_id }), [workflow_id], { live: ['assets', 'jobs'] })
  const [status, set_status] = useState<AssetFilter>(initial_status ?? 'pending')
  const [selected, set_selected] = useState<string[]>([])
  const [search, set_search] = useState('')
  const [reprocess_open, set_reprocess_open] = useState(false)

  const visible = useMemo(() => filter_assets(assets, status, search), [assets, status, search])

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
  const context_has_access = (minimum: Access) => workflows
    ? workflows.some(workflow => has_access(workflow.access, minimum))
    : current_workflow_access !== undefined && has_access(current_workflow_access, minimum)
  const selected_in_progress = assets.some(asset => selected.includes(asset.id) && asset.status === 'in_progress')
  const selected_assets = assets.filter(asset => selected.includes(asset.id))
  const selection_has_access = (minimum: Access) => selected.length > 0
    && selected_assets.length === selected.length
    && selected_assets.every(asset => {
      const workflow = workflows?.find(item => item.id === asset.workflow_id)
      const access = workflows ? (workflow ? workflow.access : undefined) : current_workflow_access
      return has_access(access, minimum)
    })
  const selected_can_run = selection_has_access('use')
  const selected_can_edit = selection_has_access('edit')
  const selected_can_manage = selection_has_access('owner')
  const actions = [
    { label: 'Ansehen', icon: 'eye', disabled: selected.length !== 1, on_click: () => open(selected[0]) },
    ...(context_has_access('edit') ? [{ label: 'Nach oben', icon: 'arrow-up', disabled: none || !selected_can_edit }] : []),
    ...(context_has_access('use') ? [
      { label: 'Erneut verarbeiten', icon: 'reprocess', disabled: none || selected_in_progress || !selected_can_run, on_click: () => set_reprocess_open(true) },
      { label: 'Abbrechen', icon: 'cancel', disabled: none || !selected_can_run },
      { label: status === 'on_hold' ? 'Freigeben' : 'Anhalten', icon: status === 'on_hold' ? 'play' : 'pause', disabled: none || !selected_can_run },
    ] : []),
    ...(context_has_access('owner') ? [{ label: 'Entfernen', icon: 'trash', disabled: none || !selected_can_manage }] : []),
    { label: 'Hinzufügen', icon: 'plus' },
    { label: 'Import', icon: 'upload' },
  ]

  return (
    <div className="asset-list">
      <StatusTabs active={status} on_change={id => { set_status(id as AssetFilter); set_selected([]) }}
        items={[
          { id: 'all', label: 'Alle', color: 'var(--text-muted)', count: assets.length },
          ...asset_statuses.map(item => ({ id: item, ...asset_status_meta[item], count: assets.filter(asset => asset.status === item).length })),
        ]} />
      <div className="asset-list-bar">
        <Toolbar actions={actions} />
        <input className="input asset-list-search" placeholder="Suchen …" value={search} onChange={event => set_search(event.target.value)} />
      </div>
      <DataTable columns={columns} rows={visible} row_key={row => row.id} selected={selected} loading={loading}
        on_selection_change={set_selected} on_row_open={row => open(row.id)}
        empty_text={status === 'all' ? 'Keine Assets' : `Keine Assets mit Status „${asset_status_meta[status].label}“`} />
      <ReprocessPanel
        open={reprocess_open && selected_can_run}
        asset_ids={selected}
        current_workflow_ids={workflow_id ? [workflow_id] : selected_assets.map(asset => asset.workflow_id)}
        on_close={() => set_reprocess_open(false)}
        on_done={() => { set_reprocess_open(false); set_selected([]); reload() }}
      />
    </div>
  )
}
