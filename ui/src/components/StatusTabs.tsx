import './StatusTabs.css'

export interface StatusTabItem {
  id: string
  label: string
  count: number
  color: string
}

interface Props {
  items: StatusTabItem[]
  active?: string
  on_change?: (id: string) => void
}

/** Row of status tiles with counters; clickable when on_change is set. */
export function StatusTabs({ items, active, on_change }: Props) {
  return (
    <div className="status-tabs" role={on_change ? 'tablist' : undefined}>
      {items.map(item => (
        <button
          key={item.id}
          type="button"
          role={on_change ? 'tab' : undefined}
          aria-selected={item.id === active}
          className={`status-tab${item.id === active ? ' active' : ''}${on_change ? '' : ' static'}`}
          style={{ ['--tab-color' as string]: item.color }}
          onClick={() => on_change?.(item.id)}
        >
          <span className="status-tab-count">{item.count}</span>
          <span className="status-tab-label">{item.label}</span>
        </button>
      ))}
    </div>
  )
}
