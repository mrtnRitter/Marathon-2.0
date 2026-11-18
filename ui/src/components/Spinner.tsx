import './Spinner.css'

export function Spinner({ active }: { active: boolean }) {
  if (!active) return null

  return (
    <div className="spinner-overlay" role="status" aria-label="Lädt">
      <span className="spinner-dot" />
      <span className="spinner-dot" />
      <span className="spinner-dot" />
    </div>
  )
}
