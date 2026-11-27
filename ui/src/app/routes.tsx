import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { AssetDetailPage } from '../pages/AssetDetailPage'
import { AssetsPage } from '../pages/AssetsPage'
import { DashboardPage } from '../pages/DashboardPage'
import { NotFoundPage } from '../pages/NotFoundPage'
import { ModulesPage } from '../pages/ModulesPage'
import { ModuleBuilderPage } from '../pages/ModuleBuilderPage'
import { ReportsPage } from '../pages/ReportsPage'
import { SettingsPage } from '../pages/SettingsPage'
import { WorkersPage } from '../pages/WorkersPage'
import { WorkflowDetailPage } from '../pages/WorkflowDetailPage'
import { WorkflowsPage } from '../pages/WorkflowsPage'
import { UsersPage } from '../pages/UsersPage'
import { RequireAdmin } from '../data/auth'

export const routes: { path: string; element: ReactNode }[] = [
  { path: '/', element: <Navigate to="/dashboard" replace /> },
  { path: '/dashboard', element: <DashboardPage /> },
  { path: '/workflows', element: <WorkflowsPage /> },
  { path: '/modules/new', element: <ModuleBuilderPage /> },
  { path: '/modules/:macro_id', element: <ModuleBuilderPage /> },
  { path: '/modules', element: <ModulesPage /> },
  { path: '/workflows/:workflow_id', element: <Navigate to="modules" replace /> },
  { path: '/workflows/:workflow_id/:tab', element: <WorkflowDetailPage /> },
  { path: '/assets', element: <AssetsPage /> },
  { path: '/assets/:asset_id', element: <AssetDetailPage /> },
  { path: '/reports', element: <ReportsPage /> },
  { path: '/workers', element: <WorkersPage /> },
  { path: '/settings', element: <RequireAdmin><SettingsPage /></RequireAdmin> },
  { path: '/users', element: <RequireAdmin><UsersPage /></RequireAdmin> },
  { path: '*', element: <NotFoundPage /> },
]
