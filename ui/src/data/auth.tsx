import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { get_auth_me } from './api'
import type { AuthUser } from './types'

interface AuthContextValue {
  user: AuthUser | null | undefined
  set_authenticated_user: (user: AuthUser) => void
  clear_authenticated_user: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, set_user] = useState<AuthUser | null | undefined>(undefined)

  useEffect(() => {
    let active = true
    void get_auth_me()
      .then(current_user => { if (active) set_user(current_user) })
      .catch(() => { if (active) set_user(null) })
    return () => { active = false }
  }, [])

  const value: AuthContextValue = {
    user,
    set_authenticated_user: authenticated_user => set_user(authenticated_user),
    clear_authenticated_user: () => set_user(null),
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

function use_auth_context(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('AuthProvider fehlt')
  return context
}

export function use_auth_user(): AuthUser | null | undefined {
  return use_auth_context().user
}

export function use_auth_actions(): Pick<AuthContextValue, 'set_authenticated_user' | 'clear_authenticated_user'> {
  const { set_authenticated_user, clear_authenticated_user } = use_auth_context()
  return { set_authenticated_user, clear_authenticated_user }
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const user = use_auth_user()
  return user?.role === 'admin' ? children : <Navigate to="/" replace />
}
