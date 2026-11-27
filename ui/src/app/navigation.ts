// Central registry of sidebar entries. A new page needs one entry here and one route in routes.tsx.
export interface NavItem {
  path: string
  label: string
  icon: string
  admin_only?: boolean
}

export const nav_groups: { title: string; items: NavItem[] }[] = [
  {
    title: 'Übersicht',
    items: [
      { path: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
      { path: '/workflows', label: 'Workflows', icon: 'workflow' },
      { path: '/modules', label: 'Module', icon: 'blocks' },
      { path: '/assets', label: 'Assets', icon: 'files' },
      { path: '/reports', label: 'Reports', icon: 'report' },
    ],
  },
  {
    title: 'System',
    items: [
      { path: '/workers', label: 'Worker', icon: 'server' },
      { path: '/settings', label: 'Einstellungen', icon: 'settings', admin_only: true },
      { path: '/users', label: 'Nutzer', icon: 'users', admin_only: true },
    ],
  },
]
