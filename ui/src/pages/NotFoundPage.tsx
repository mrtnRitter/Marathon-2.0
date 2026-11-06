import { Link } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="404" />
      <div className="page-content"><p className="muted">Diese Seite gibt es nicht. <Link to="/dashboard">Zum Dashboard</Link></p></div>
    </>
  )
}
