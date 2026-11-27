import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { notify } from '../components/Toast'
import { delete_macro, get_macros } from '../data/api'
import type { MacroSummary } from '../data/types'
import { use_data } from '../data/use_data'
import { format_datetime } from '../lib/format'
import { can_delete_macro, macro_owner_label, macro_usage_label } from '../lib/macros'
import '../components/PrioListField.css'

export function ModulesPage() {
  const navigate = useNavigate()
  const { data, reload } = use_data(get_macros)
  const [open_menu, set_open_menu] = useState<string | null>(null)
  const [deleting_id, set_deleting_id] = useState<string | null>(null)

  const open_macro = (macro: MacroSummary) => {
    if (macro.can_edit) navigate(`/modules/${encodeURIComponent(macro.id)}`)
  }

  const delete_selected = async (macro: MacroSummary) => {
    if (!can_delete_macro(macro) || deleting_id !== null) return
    if (!window.confirm(`Modul „${macro.name}“ löschen?`)) return
    set_open_menu(null)
    set_deleting_id(macro.id)
    try {
      await delete_macro(macro.id)
      notify('Modul gelöscht')
      reload()
    } catch {
      // The request layer already reports deletion failures.
    } finally {
      set_deleting_id(null)
    }
  }

  const rows = data?.map(macro => {
    const can_edit = macro.can_edit
    return (
      <tr key={macro.id} onClick={() => open_macro(macro)} onKeyDown={event => {
        if (can_edit && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          open_macro(macro)
        }
      }} tabIndex={can_edit ? 0 : undefined} role={can_edit ? 'link' : undefined}
      aria-label={can_edit ? `Modul ${macro.name} öffnen` : undefined} style={{ cursor: can_edit ? 'pointer' : 'default' }}>
        <td><span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)' }}><Icon name={macro.icon} size={16} /><strong>{macro.name}</strong></span></td>
        <td>{macro.category}</td>
        <td>{macro_owner_label(macro.owner)}</td>
        <td>v{macro.version}</td>
        <td>{macro_usage_label(macro.usage)}</td>
        <td>{format_datetime(macro.updated_at)}</td>
        <td>
          {can_delete_macro(macro) && <div className="priolist-actions" onClick={event => event.stopPropagation()}>
            <button type="button" className="priolist-icon-button" aria-label={`Weitere Aktionen für ${macro.name}`} aria-haspopup="menu"
              aria-expanded={open_menu === macro.id} disabled={deleting_id === macro.id}
              onClick={() => set_open_menu(open_menu === macro.id ? null : macro.id)}>
              <Icon name="more" />
            </button>
            {open_menu === macro.id && <div className="priolist-menu" role="menu">
              <button type="button" role="menuitem" onClick={() => { void delete_selected(macro) }}>
                <Icon name="trash" /> Löschen
              </button>
            </div>}
          </div>}
        </td>
      </tr>
    )
  })

  return (
    <>
      <PageHeader title="Module" actions={[{ label: 'Neues Modul', icon: 'plus', primary: true, on_click: () => navigate('/modules/new') }]} />
      <div className="page-content">
        {!data ? <div className="loading">Lade</div> : (
          <div className="data-table-frame">
            <div className="data-table-wrap">
              <table className="data-table">
                <thead><tr>
                  <th>Name</th><th>Kategorie</th><th>Eigentümer</th><th>Version</th><th>Verwendet</th><th>Geändert</th><th aria-label="Aktionen" />
                </tr></thead>
                <tbody>{rows}</tbody>
              </table>
              {data.length === 0 && <div className="data-table-empty">Keine Module vorhanden</div>}
            </div>
          </div>
        )}
      </div>
    </>
  )
}
