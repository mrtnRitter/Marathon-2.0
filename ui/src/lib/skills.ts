export function move_skill(list: string[], index: number, delta: number): string[] {
  const target = index + delta
  if (!Number.isInteger(index) || !Number.isInteger(delta) || index < 0 || index >= list.length || target < 0 || target >= list.length) return [...list]
  const updated = [...list]
  ;[updated[index], updated[target]] = [updated[target], updated[index]]
  return updated
}

export function remove_skill(list: string[], index: number): string[] {
  return Number.isInteger(index) && index >= 0 && index < list.length ? list.filter((_, item_index) => item_index !== index) : [...list]
}

export function add_skill(list: string[], id: string): string[] {
  return list.includes(id) ? [...list] : [...list, id]
}

export function available_skills(capabilities: string[], list: string[]): string[] {
  const listed = new Set(list)
  return capabilities.filter(id => !listed.has(id))
}

export function shown_skills(skills: string[], capabilities: string[]): string[] {
  const supported = new Set(capabilities)
  return skills.filter(id => supported.has(id))
}
