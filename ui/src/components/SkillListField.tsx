import { useState } from 'react'
import type { ModuleType } from '../data/types'
import { add_skill, available_skills, move_skill, remove_skill, shown_skills } from '../lib/skills'
import { Icon } from './Icon'
import './SkillListField.css'

interface Props {
  value: string[]
  capabilities: string[]
  modules: Pick<ModuleType, 'id' | 'name'>[]
  on_change: (value: string[]) => void
  disabled?: boolean
}

export function SkillListField({ value, capabilities, modules, on_change, disabled = false }: Props) {
  const [new_skill, set_new_skill] = useState('')
  const skills = shown_skills(value, capabilities)
  const available = available_skills(capabilities, skills)
  const module_name = (id: string) => modules.find(module => module.id === id)?.name || id

  return (
    <div className="skill-list-field">
      {skills.length === 0 ? <div className="skill-list-empty">Noch keine Fähigkeiten</div> : (
        <table className="skill-list-table">
          <thead><tr><th scope="col">Prio</th><th scope="col">Modultyp</th><th scope="col" aria-label="Aktionen" /></tr></thead>
          <tbody>
            {skills.map((id, index) => (
              <tr key={`${id}-${index}`}>
                <td className="skill-list-number">{index + 1}</td>
                <td>{module_name(id)}</td>
                <td>
                  <div className="skill-list-actions">
                    <button type="button" className="skill-list-icon-button" aria-label="Hoch" disabled={disabled || index === 0} onClick={() => on_change(move_skill(skills, index, -1))}>
                      <Icon name="chevron-up" size={16} />
                    </button>
                    <button type="button" className="skill-list-icon-button" aria-label="Runter" disabled={disabled || index === skills.length - 1} onClick={() => on_change(move_skill(skills, index, 1))}>
                      <Icon name="chevron-down" size={16} />
                    </button>
                    <button type="button" className="skill-list-icon-button" aria-label={`Entfernen: ${module_name(id)}`} disabled={disabled} onClick={() => on_change(remove_skill(skills, index))}>
                      <Icon name="close" size={16} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="skill-list-add">
        <select className="input" aria-label="Fähigkeit hinzufügen" value={new_skill} disabled={disabled || available.length === 0} onChange={event => set_new_skill(event.target.value)}>
          <option value="">Modultyp wählen</option>
          {available.map(id => <option key={id} value={id}>{module_name(id)}</option>)}
        </select>
        <button type="button" className="button" disabled={disabled || !new_skill || !available.includes(new_skill)} onClick={() => { on_change(add_skill(skills, new_skill)); set_new_skill('') }}>
          Fähigkeit hinzufügen
        </button>
      </div>
    </div>
  )
}
