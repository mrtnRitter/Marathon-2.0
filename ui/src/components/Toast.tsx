import { useEffect, useState } from 'react'
import './Toast.css'

interface ToastItem { id: number; text: string }

let next_id = 1
const listeners = new Set<(items: ToastItem[]) => void>()
let items: ToastItem[] = []

const publish = () => listeners.forEach(listener => listener(items))

/** Show a short message at the bottom right. */
export function notify(text: string) {
  const item = { id: next_id++, text }
  items = [...items, item]
  publish()
  setTimeout(() => { items = items.filter(entry => entry !== item); publish() }, 2600)
}

export const not_available = () => notify('Noch nicht verfügbar')

export function ToastHost() {
  const [visible, set_visible] = useState(items)
  useEffect(() => { listeners.add(set_visible); return () => { listeners.delete(set_visible) } }, [])
  return (
    <div className="toast-host" aria-live="polite">
      {visible.map(item => <div key={item.id} className="toast">{item.text}</div>)}
    </div>
  )
}
