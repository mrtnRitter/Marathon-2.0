// Deterministic pseudo random numbers so the sample data stays stable between reloads
export function create_random(seed: number) {
  let state = seed
  const next = () => {
    state = (state * 1664525 + 1013904223) % 4294967296
    return state / 4294967296
  }
  return {
    next,
    int: (min: number, max: number) => min + Math.floor(next() * (max - min + 1)),
    pick: <T,>(items: readonly T[]) => items[Math.floor(next() * items.length)],
  }
}

const now = Date.now()

export const minutes_ago = (minutes: number) => new Date(now - minutes * 60_000).toISOString()
