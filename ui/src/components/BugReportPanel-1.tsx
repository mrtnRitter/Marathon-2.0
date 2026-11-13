import { useEffect, useState } from 'react'
import { AutoTextarea } from './AutoTextarea'
import { FormField } from './FormField'
import { SlidePanel } from './SlidePanel'
import { notify } from './Toast'

const bug_report_template = 'Was wolltest Du machen?\n\n\nWas hast Du erwartet?\n\n\nWas ist passiert?\n\n'

export function BugReportPanel({ open, on_close }: { open: boolean; on_close: () => void }) {
  const [subject, set_subject] = useState('')
  const [text, set_text] = useState('')

  useEffect(() => {
    if (!open) return
    set_subject('')
    set_text(bug_report_template)
  }, [open])

  const send = () => {
    on_close()
    notify('Bug-Report wurde versendet')
  }

  return (
    <SlidePanel
      open={open}
      title="Bug melden"
      icon="cat"
      on_close={on_close}
      actions={[
        { label: 'Abbrechen', icon: 'close', on_click: on_close },
        { label: 'Senden', icon: 'send', primary: true, on_click: send, disabled: !subject.trim() },
      ]}
    >
      <FormField
        spec={{ key: 'bug_subject', label: 'Betreff', type: 'text' }}
        value={subject}
        on_change={value => set_subject(String(value))}
      />
      <div className="form-field">
        <label htmlFor="field-bug_text" className="form-field-label">Text</label>
        <div className="form-field-control">
          <AutoTextarea id="field-bug_text" value={text} on_change={set_text} />
        </div>
      </div>
    </SlidePanel>
  )
}
