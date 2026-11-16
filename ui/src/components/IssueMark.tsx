import { Icon } from './Icon'
import { issue_title } from '../lib/workflow_issues'
import './IssueMark.css'

export function IssueMark({ issues }: { issues: string[] }) {
  if (issues.length === 0) return null
  const title = issue_title(issues)
  return <span className="issue-mark" title={title} aria-label={title}><Icon name="alert" size={14} /></span>
}
