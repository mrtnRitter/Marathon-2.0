import { useMemo, useState, type DragEvent } from 'react'
import { Icon } from '../../components/Icon'
import type { ModuleType } from '../../data/types'
import { drag_mime } from './editor_types'

/** Right-hand list of module types; entries are dragged onto the canvas. */
export function ModulePalette({ module_types }: { module_types: ModuleType[] }) {
  const [search, set_search] = useState('')
  const groups = useMemo(() => {
    const term = search.trim().toLowerCase()
    const matches = module_types.filter(type => !term || `${type.name} ${type.category} ${type.description}`.toLowerCase().includes(term))
    return [...new Set(matches.map(type => type.category))].map(category => ({ category, items: matches.filter(type => type.category === category) }))
  }, [module_types, search])

  const start_drag = (event: DragEvent, module_type: ModuleType) => {
    event.dataTransfer.setData(drag_mime, module_type.id)
    event.dataTransfer.effectAllowed = 'move'
  }

  return (
    <aside className="module-palette">
      <div className="module-palette-search">
        <Icon name="search" size={14} />
        <input className="input" placeholder="Module suchen …" value={search} onChange={event => set_search(event.target.value)} />
      </div>
      <div className="module-palette-list">
        {groups.map(group => (
          <div key={group.category} className="module-palette-group">
            <div className="module-palette-category">{group.category}</div>
            {group.items.map(type => (
              <div key={type.id} className="module-palette-item" draggable onDragStart={event => start_drag(event, type)} title={type.description}>
                <span className="module-node-icon"><Icon name={type.icon} size={14} /></span>
                <span>{type.name}</span>
              </div>
            ))}
          </div>
        ))}
        {groups.length === 0 && <div className="dim">Keine Treffer</div>}
      </div>
      <div className="module-palette-hint dim">Ziehen = hinzufügen · Doppelklick = Einstellungen · Entf = löschen</div>
    </aside>
  )
}
