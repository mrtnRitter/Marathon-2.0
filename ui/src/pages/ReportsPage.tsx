import { useState } from 'react'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { StatusTabs } from '../components/StatusTabs'
import '../components/DataTable.css'
import { get_job_status_report, get_workflows, job_status_csv_url } from '../data/api'
import { use_data } from '../data/use_data'
import { job_status_meta, job_statuses } from '../theme/status'
import './ReportsPage.css'

function format_date(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function parse_local_date(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return null
  const [, year_text, month_text, day_text] = match
  const year = Number(year_text)
  const month = Number(month_text)
  const day = Number(day_text)
  const date = new Date(0)
  date.setFullYear(year, month - 1, day)
  date.setHours(0, 0, 0, 0)
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null
}

const percent_format = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

function ReportResults({ start, end, workflow_id }: { start: string; end: string; workflow_id: string }) {
  const report_state = use_data(
    () => get_job_status_report({ start, end, workflow_id: workflow_id || undefined }),
    [start, end, workflow_id],
  )
  if (report_state.loading) return <div className="loading">Lade</div>
  if (report_state.error) return <div className="data-table-empty">Report konnte nicht geladen werden</div>
  const report = report_state.data
  if (!report) return null

  return <>
    <StatusTabs items={job_statuses.map(status => ({
      id: status,
      label: job_status_meta[status].label,
      color: job_status_meta[status].color,
      count: report.totals.counts[status],
    }))} />
    {report.rows.length ? <div className="data-table-wrap"><table className="data-table reports-table">
      <thead><tr>
        <th>Workflow</th>
        <th className="num">Gesamt</th>
        {job_statuses.map(status => <th className="num" key={status}>{job_status_meta[status].label}</th>)}
        <th className="num">% erfolgreich</th>
      </tr></thead>
      <tbody>{report.rows.map(row => <tr key={row.workflow_id}>
        <td>{row.workflow_name}</td>
        <td className="num">{row.total}</td>
        {job_statuses.map(status => <td className="num" key={status}>{row.counts[status]}</td>)}
        <td className="num">{row.total ? `${percent_format.format(row.counts.succeeded / row.total * 100)} %` : '–'}</td>
      </tr>)}</tbody>
      <tfoot><tr>
        <td>Summe</td>
        <td className="num">{report.totals.total}</td>
        {job_statuses.map(status => <td className="num" key={status}>{report.totals.counts[status]}</td>)}
        <td className="num">{report.totals.total ? `${percent_format.format(report.totals.counts.succeeded / report.totals.total * 100)} %` : '–'}</td>
      </tr></tfoot>
    </table></div> : <div className="data-table-empty">Keine Workflows</div>}
  </>
}

export function ReportsPage() {
  const [dates, set_dates] = useState(() => {
    const today = new Date()
    return {
      from: format_date(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6)),
      to: format_date(today),
    }
  })
  const [workflow_id, set_workflow_id] = useState('')
  const { data: workflows = [] } = use_data(get_workflows)
  const from_date = parse_local_date(dates.from)
  const to_date = parse_local_date(dates.to)
  const range_hint = !dates.from || !dates.to
    ? 'Bitte Zeitraum wählen'
    : !from_date || !to_date ? 'Bitte gültige Datumsangaben wählen'
      : dates.to < dates.from ? '„Bis“ darf nicht vor „Von“ liegen' : null
  const range = range_hint || !from_date || !to_date ? null : (() => {
    const end_date = new Date(to_date)
    end_date.setDate(end_date.getDate() + 1)
    return { start: from_date.toISOString(), end: end_date.toISOString() }
  })()

  return (
    <>
      <PageHeader title="Reports" />
      <div className="page-content reports-page">
        <div className="reports-filter">
          <label>Von <input type="date" className="input" value={dates.from} max={dates.to}
            onChange={event => set_dates(previous => ({ ...previous, from: event.target.value }))} /></label>
          <label>Bis <input type="date" className="input" value={dates.to} min={dates.from}
            onChange={event => set_dates(previous => ({ ...previous, to: event.target.value }))} /></label>
          <label>Workflow <select className="input" value={workflow_id} onChange={event => set_workflow_id(event.target.value)}>
            <option value="">Alle Workflows</option>
            {workflows.map(workflow => <option key={workflow.id} value={workflow.id}>{workflow.name}</option>)}
          </select></label>
          {range && <a className="button" href={job_status_csv_url({ start: range.start, end: range.end, workflow_id: workflow_id || undefined })}
            download={`job-status_${dates.from}_${dates.to}.csv`}><Icon name="download" size={14} /><span>CSV herunterladen</span></a>}
          {range_hint && <div className="reports-hint">{range_hint}</div>}
        </div>
        <p className="dim reports-note">Jobs, die im Zeitraum angelegt wurden, nach aktuellem Status</p>
        {range && <ReportResults key={`${range.start}|${range.end}|${workflow_id}`} start={range.start} end={range.end} workflow_id={workflow_id} />}
      </div>
    </>
  )
}
