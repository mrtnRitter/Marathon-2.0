import { NavLink } from 'react-router-dom'
import { nav_groups } from '../app/navigation'
import { Icon } from './Icon'
import './Sidebar.css'

export function Sidebar({ collapsed, on_toggle }: { collapsed: boolean; on_toggle: () => void }) {
  return (
    <nav className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <div className="sidebar-brand">
        <img className="sidebar-logo" src="/favicon.svg" alt="" width={30} height={30} />
        {!collapsed && <span className="sidebar-name">marathon<small>v{__APP_VERSION__}</small></span>}
      </div>
      {nav_groups.map(group => (
        <div key={group.title} className="sidebar-group">
          {!collapsed && <div className="sidebar-group-title">{group.title}</div>}
          {group.items.map(item => (
            <NavLink key={item.path} to={item.path} className="sidebar-link" title={collapsed ? item.label : undefined}>
              <Icon name={item.icon} size={17} />
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
        </div>
      ))}
      <button type="button" className="sidebar-toggle" onClick={on_toggle} title={collapsed ? 'Ausklappen' : 'Einklappen'}>
        <Icon name={collapsed ? 'chevron-right' : 'chevron-left'} />
      </button>
    </nav>
  )
}
