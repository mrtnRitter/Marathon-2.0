import { useEffect, useState } from 'react'
import './Spinner.css'

export function Spinner({ active }: { active: boolean }) {
  const [visible, set_visible] = useState(false)

  useEffect(() => {
    if (!active) {
      set_visible(false)
      return
    }
    const timer = window.setTimeout(() => set_visible(true), 500)
    return () => window.clearTimeout(timer)
  }, [active])

  if (!active || !visible) return null

  return (
    <div className="spinner-overlay" role="status" aria-label="Lädt">
      <span className="spinner-dot" />
      <span className="spinner-dot" />
      <span className="spinner-dot" />
    </div>
  )
}
