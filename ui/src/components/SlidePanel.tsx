import { useEffect, type ReactNode } from 'react'
import { Icon } from './Icon'
import { Toolbar, type ToolbarAction } from './Toolbar'
import './SlidePanel.css'

interface Props {
  open: boolean
  title: ReactNode
  icon?: string
  on_close: () => void
  actions?: ToolbarAction[]
  width?: number
  children: ReactNode
}

/** Editor panel that slides in from the right. Escape or the backdrop closes it. */
export function SlidePanel({ open, title, icon, on_close, actions = [], width = 520, children }: Props) {
  useEffect(() => {
    if (!open) return
    const on_key = (event: KeyboardEvent) => event.key === 'Escape' && on_close()
    window.addEventListener('keydown', on_key)
    return () => window.removeEventListener('keydown', on_key)
  }, [open, on_close])

  if (!open) return null
  return (
    <div className="slide-panel-backdrop" onMouseDown={on_close}>
      <aside className="slide-panel" style={{ width }} onMouseDown={event => event.stopPropagation()}>
        <header className="slide-panel-header">
          {icon && <span className="slide-panel-icon"><Icon name={icon} size={16} /></span>}
          <h2>{title}</h2>
          <button type="button" className="slide-panel-close" onClick={on_close} aria-label="Schließen"><Icon name="close" /></button>
        </header>
        <div className="slide-panel-body">{children}</div>
        {actions.length > 0 && <footer className="slide-panel-footer"><Toolbar actions={actions} /></footer>}
      </aside>
    </div>
  )
}
