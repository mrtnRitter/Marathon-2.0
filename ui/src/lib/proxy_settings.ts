export function parse_proxy_addresses(text: string): string[] {
  return text.split(',').map(address => address.trim()).filter(Boolean)
}

export function format_proxy_addresses(addresses: string[]): string {
  return addresses.join(', ')
}
