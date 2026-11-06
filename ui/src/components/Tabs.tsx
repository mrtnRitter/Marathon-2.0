import { Icon } from './Icon'
import './Tabs.css'

export interface TabItem {
  id: string
  label: string
  icon?: string
  count?: number
}

export function Tabs({ items, active, on_change }: { items: TabItem[]; active: string; on_change: (id: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {items.map(item => (
        <button key={item.id} type="button" role="tab" aria-selected={item.id === active}
          className={`tab${item.id === active ? ' active' : ''}`} onClick={() => on_change(item.id)}>
          {item.icon && <Icon name={item.icon} size={14} />}
          {item.label}
          {item.count !== undefined && <span className="tab-count">{item.count}</span>}
        </button>
      ))}
    </div>
  )
}
