export function server_root(address: string): string | null {
  const trimmed_address = address.trim()
  return trimmed_address ? `\\\\${trimmed_address}` : null
}

export function folder_mode(path: string | null | undefined): 'server' | 'drives' {
  return path?.startsWith('\\\\') ? 'server' : 'drives'
}
