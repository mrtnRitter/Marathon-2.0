import { Icon } from './Icon'
import { not_available } from './Toast'
import './Toolbar.css'

export interface ToolbarAction {
  label: string
  icon: string
  on_click?: () => void
  disabled?: boolean
  primary?: boolean
}

export function Button({ label, icon, on_click = not_available, disabled, primary }: ToolbarAction) {
  return (
    <button type="button" className={`button${primary ? ' button-primary' : ''}`} onClick={on_click} disabled={disabled}>
      <Icon name={icon} size={14} />
      <span>{label}</span>
    </button>
  )
}

export function Toolbar({ actions }: { actions: ToolbarAction[] }) {
  return <div className="toolbar">{actions.map(action => <Button key={action.label} {...action} />)}</div>
}
