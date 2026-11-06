import type { ReactNode } from 'react'
import { Toolbar, type ToolbarAction } from './Toolbar'
import './PageHeader.css'

interface Props {
  title: ReactNode
  breadcrumb?: ReactNode
  actions?: ToolbarAction[]
  children?: ReactNode
}

export function PageHeader({ title, breadcrumb, actions = [], children }: Props) {
  return (
    <header className="page-header">
      <div className="page-header-title">
        {breadcrumb && <div className="page-header-breadcrumb">{breadcrumb}</div>}
        <h1>{title}</h1>
      </div>
      <div className="page-header-tools">
        {children}
        {actions.length > 0 && <Toolbar actions={actions} />}
      </div>
    </header>
  )
}
