import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { AssetDetailPage } from '../pages/AssetDetailPage'
import { AssetsPage } from '../pages/AssetsPage'
import { DashboardPage } from '../pages/DashboardPage'
import { NotFoundPage } from '../pages/NotFoundPage'
import { SettingsPage } from '../pages/SettingsPage'
import { WorkersPage } from '../pages/WorkersPage'
import { WorkflowDetailPage } from '../pages/WorkflowDetailPage'
import { WorkflowsPage } from '../pages/WorkflowsPage'

export const routes: { path: string; element: ReactNode }[] = [
  { path: '/', element: <Navigate to="/dashboard" replace /> },
  { path: '/dashboard', element: <DashboardPage /> },
  { path: '/workflows', element: <WorkflowsPage /> },
  { path: '/workflows/:workflow_id', element: <Navigate to="modules" replace /> },
  { path: '/workflows/:workflow_id/:tab', element: <WorkflowDetailPage /> },
  { path: '/assets', element: <AssetsPage /> },
  { path: '/assets/:asset_id', element: <AssetDetailPage /> },
  { path: '/workers', element: <WorkersPage /> },
  { path: '/settings', element: <SettingsPage /> },
  { path: '*', element: <NotFoundPage /> },
]
