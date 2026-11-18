export function filter_dirs<T extends { path: string }>(dirs: T[], listing_path: string | null, input: string): T[] {
  const normalize = (value: string) => value.replaceAll('/', '\\').toLowerCase()
  const query = normalize(input.trim())
  if (!query) return dirs
  if (listing_path !== null && !query.startsWith(normalize(listing_path))) return dirs
  return dirs.filter(dir => normalize(dir.path).startsWith(query))
}
