import type { AssetStatus } from '../data/types'

export type AssetFilter = AssetStatus | 'all'

export function filter_assets<T extends { status: AssetStatus; name: string }>(assets: T[], status: AssetFilter, search: string): T[] {
  const query = search.trim().toLowerCase()
  return assets.filter(asset => (status === 'all' || asset.status === status) && asset.name.toLowerCase().includes(query))
}
