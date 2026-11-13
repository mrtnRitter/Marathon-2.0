import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { PrioEntry } from '../data/types'
import { Icon } from './Icon'
import { not_available } from './Toast'
import './PrioListField.css'

interface Props {
  value: PrioEntry[]
  on_change: (value: PrioEntry[]) => void
  disabled?: boolean
}

export function PrioListField({ value, on_change, disabled = false }: Props) {
  const root_ref = useRef<HTMLDivElement>(null)
  const name_refs = useRef<Array<HTMLInputElement | null>>([])
  const [open_menu, set_open_menu] = useState<number | null>(null)
  const [focus_index, set_focus_index] = useState<number | null>(null)

  useEffect(() => {
    if (disabled || open_menu === null) return
    const close_on_outside_click = (event: PointerEvent) => {
      if (event.target instanceof Node && !root_ref.current?.contains(event.target)) set_open_menu(null)
    }
    const close_on_escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') set_open_menu(null)
    }
    document.addEventListener('pointerdown', close_on_outside_click)
    document.addEventListener('keydown', close_on_escape)
    return () => {
      document.removeEventListener('pointerdown', close_on_outside_click)
      document.removeEventListener('keydown', close_on_escape)
    }
  }, [disabled, open_menu])

  useEffect(() => {
    if (disabled) set_open_menu(null)
  }, [disabled])

  useLayoutEffect(() => {
    if (focus_index === null) return
    name_refs.current[focus_index]?.focus()
    set_focus_index(null)
  }, [focus_index, value])

  const move_entry = (index: number, offset: -1 | 1) => {
    const target = index + offset
    if (target < 0 || target >= value.length) return
    const updated = [...value]
    ;[updated[index], updated[target]] = [updated[target], updated[index]]
    on_change(updated)
  }

  const add_entry = () => {
    if (disabled) return
    on_change([...value, { name: '', metadata: {} }])
    set_open_menu(null)
    set_focus_index(value.length)
  }

  return (
    <div className="priolist-field" ref={root_ref}>
      {value.length === 0 ? <div className="priolist-empty">Noch keine Einträge</div> : (
        <table className="priolist-table">
          <thead>
            <tr><th scope="col">Prio</th><th scope="col">Name</th><th scope="col" aria-label="Aktionen" /></tr>
          </thead>
          <tbody>
            {value.map((entry, index) => (
              <tr key={index}>
                <td className="priolist-number">{index + 1}</td>
                <td>
                  <input
                    ref={element => { name_refs.current[index] = element }}
                    className="input"
                    aria-label={`Name Eintrag ${index + 1}`}
                    value={entry.name}
                    disabled={disabled}
                    onChange={event => on_change(value.map((item, item_index) => item_index === index ? { ...item, name: event.target.value } : item))}
                  />
                </td>
                <td>
                  <div className="priolist-actions">
                    <button type="button" className="priolist-icon-button" aria-label="Hoch" disabled={disabled || index === 0} onClick={() => move_entry(index, -1)}>
                      <Icon name="chevron-up" />
                    </button>
                    <button type="button" className="priolist-icon-button" aria-label="Runter" disabled={disabled || index === value.length - 1} onClick={() => move_entry(index, 1)}>
                      <Icon name="chevron-down" />
                    </button>
                    <button
                      type="button"
                      className="priolist-icon-button"
                      aria-label="Weitere Aktionen"
                      aria-haspopup="menu"
                      aria-expanded={!disabled && open_menu === index}
                      disabled={disabled}
                      onClick={() => set_open_menu(open_menu === index ? null : index)}
                    >
                      <Icon name="more" />
                    </button>
                    {!disabled && open_menu === index && (
                      <div className="priolist-menu" role="menu">
                        <button type="button" role="menuitem" onClick={() => { set_open_menu(null); not_available() }}>
                          Metadaten bearbeiten
                        </button>
                        <button type="button" role="menuitem" onClick={() => {
                          on_change(value.filter((_, item_index) => item_index !== index))
                          set_open_menu(null)
                        }}>
                          <Icon name="trash" /> Entfernen
                        </button>
                      </div>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button type="button" className="button priolist-add" disabled={disabled} onClick={add_entry}>
        <Icon name="plus" size={14} />
        <span>Eintrag hinzufügen</span>
      </button>
    </div>
  )
}
