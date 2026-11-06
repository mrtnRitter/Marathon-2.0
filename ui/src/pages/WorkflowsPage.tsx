import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DataTable, type Column } from '../components/DataTable'
import { PageHeader } from '../components/PageHeader'
import { StatusBadge } from '../components/StatusBadge'
import { StatusBar } from '../components/StatusBar'
import { get_workflows } from '../data/api'
import type { Workflow } from '../data/types'
import { use_data } from '../data/use_data'
import { active_meta, asset_statuses } from '../theme/status'

const total = (workflow: Workflow) => asset_statuses.reduce((sum, status) => sum + workflow.asset_counts[status], 0)

const columns: Column<Workflow>[] = [
  { key: 'name', label: 'Name', render: row => <strong>{row.name}</strong> },
  { key: 'active', label: 'Status', render: row => <StatusBadge meta={active_meta(row.active)} />, sort_value: row => Number(row.active) },
  { key: 'version', label: 'Version', render: row => `v${row.version}`, align: 'right' },
  { key: 'priority', label: 'Priorität', align: 'right' },
  { key: 'assets', label: 'Assets', render: row => total(row), sort_value: total, align: 'right' },
  { key: 'distribution', label: 'Verteilung', render: row => <StatusBar counts={row.asset_counts} />, width: '22%' },
  { key: 'description', label: 'Beschreibung', render: row => <span className="dim">{row.description}</span> },
]

export function WorkflowsPage() {
  const navigate = useNavigate()
  const { data } = use_data(get_workflows)
  const [selected, set_selected] = useState<string[]>([])
  const open = () => selected[0] && navigate(`/workflows/${selected[0]}/modules`)
  return (
    <>
      <PageHeader title="Workflows" actions={[
        { label: 'Neu', icon: 'plus', primary: true },
        { label: 'Öffnen', icon: 'pencil', on_click: open, disabled: selected.length !== 1 },
        { label: 'Duplizieren', icon: 'copy', disabled: selected.length !== 1 },
        { label: 'Pausieren/Aktivieren', icon: 'pause', disabled: !selected.length },
        { label: 'Löschen', icon: 'trash', disabled: !selected.length },
      ]} />
      <div className="page-content">
        {!data ? <div className="loading">Lade</div> : (
          <DataTable columns={columns} rows={data} row_key={row => row.id} selected={selected}
            on_selection_change={set_selected} on_row_open={row => navigate(`/workflows/${row.id}/modules`)} />
        )}
      </div>
    </>
  )
}
