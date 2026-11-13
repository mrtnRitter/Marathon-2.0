import { useLayoutEffect, useRef } from 'react'
import './AutoTextarea.css'

export function AutoTextarea({ id, value, on_change, disabled = false, className }: { id?: string; value: string; on_change: (value: string) => void; disabled?: boolean; className?: string }) {
  const textarea_ref = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    const element = textarea_ref.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [value])

  return (
    <textarea
      ref={textarea_ref}
      id={id}
      className={`input auto-textarea${className ? ` ${className}` : ''}`}
      rows={1}
      value={value}
      disabled={disabled}
      onChange={event => on_change(event.target.value)}
    />
  )
}
