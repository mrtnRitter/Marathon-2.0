import { useEffect, useRef, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { nav_groups } from '../app/navigation'
import { BugReportPanel } from './BugReportPanel'
import { Icon } from './Icon'
import { use_auth_actions, use_auth_user } from '../data/auth'
import { logout } from '../data/api'
import './Sidebar.css'

const cat_sounds = ['Murr', 'Mautz', 'Miau', 'Mau', 'Greep', 'Brrr', 'Mrrp', 'Prrr'] as const
const cat_bubble_duration_ms = 1500
const cat_bubble_fade_ms = 300

export function pick_cat_sound(random: () => number = Math.random): string {
  const index = Math.min(Math.max(Math.floor(random() * cat_sounds.length), 0), cat_sounds.length - 1)
  return cat_sounds[index]
}

export function Sidebar({ collapsed, on_toggle }: { collapsed: boolean; on_toggle: () => void }) {
  const user = use_auth_user()
  const user_name = user?.email || user?.display_name || ''
  const { clear_authenticated_user } = use_auth_actions()
  const navigate = useNavigate()
  const [logging_out, set_logging_out] = useState(false)
  const [bug_open, set_bug_open] = useState(false)
  const [cat_bubble, set_cat_bubble] = useState<{ sound: string; id: number; fading: boolean } | null>(null)
  const next_bubble_id = useRef(0)

  useEffect(() => {
    if (!cat_bubble) return
    const bubble_id = cat_bubble.id
    const fade_timer = window.setTimeout(() => {
      set_cat_bubble(current => current?.id === bubble_id ? { ...current, fading: true } : current)
    }, cat_bubble_duration_ms - cat_bubble_fade_ms)
    const hide_timer = window.setTimeout(() => {
      set_cat_bubble(current => current?.id === bubble_id ? null : current)
    }, cat_bubble_duration_ms)
    return () => {
      window.clearTimeout(fade_timer)
      window.clearTimeout(hide_timer)
    }
  }, [cat_bubble?.id])

  const visible_groups = nav_groups
    .map(group => ({ ...group, items: group.items.filter(item => !item.admin_only || user?.role === 'admin') }))
    .filter(group => group.items.length > 0)

  const sign_out = async () => {
    set_logging_out(true)
    try {
      await logout()
      navigate('/login', { replace: true })
      clear_authenticated_user()
    } catch {
      // The API client already reports the error.
    } finally {
      set_logging_out(false)
    }
  }

  const open_bug_report = () => {
    next_bubble_id.current += 1
    set_cat_bubble({ sound: pick_cat_sound(), id: next_bubble_id.current, fading: false })
    set_bug_open(true)
  }

  return (
    <nav className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <div className="sidebar-brand">
        <img className="sidebar-logo" src="/favicon.svg" alt="" width={30} height={30} />
        {!collapsed && <span className="sidebar-name">marathon<small>v{__APP_VERSION__}</small></span>}
      </div>
      {visible_groups.map(group => (
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
      <div className="sidebar-footer">
        <div className="sidebar-account">
          {!collapsed && <span className="sidebar-email" title={user_name}>{user_name}</span>}
          <button type="button" className="sidebar-link sidebar-sign-out" onClick={() => { void sign_out() }} disabled={logging_out} title={collapsed ? `${user_name} · Abmelden` : undefined} aria-label="Abmelden">
            <Icon name="log-out" size={17} />
            {!collapsed && <span>Abmelden</span>}
          </button>
        </div>
        <div className="sidebar-bug-container">
          {cat_bubble && <div className={`sidebar-cat-bubble${cat_bubble.fading ? ' fading' : ''}`} aria-live="polite">{cat_bubble.sound}</div>}
          <button type="button" className="sidebar-link sidebar-bug" onClick={open_bug_report} title={collapsed ? 'Bug melden' : undefined}>
            <Icon name="cat" size={17} />
            {!collapsed && <span>Bug melden</span>}
          </button>
        </div>
      </div>
      <button type="button" className="sidebar-toggle" onClick={on_toggle} title={collapsed ? 'Ausklappen' : 'Einklappen'}>
        <Icon name={collapsed ? 'chevron-right' : 'chevron-left'} />
      </button>
      <BugReportPanel open={bug_open} on_close={() => set_bug_open(false)} />
    </nav>
  )
}
