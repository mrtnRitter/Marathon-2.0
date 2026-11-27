export type ShareLevel = 'view' | 'use' | 'edit'

export function build_shares(
  enabled: boolean,
  all_access: ShareLevel | '',
  user_access: Record<string, ShareLevel | ''>,
  owner_id: string | null,
): { user_id: string | null; access: ShareLevel }[] {
  if (!enabled) return []

  const shares: { user_id: string | null; access: ShareLevel }[] = []
  if (all_access) shares.push({ user_id: null, access: all_access })
  for (const [user_id, access] of Object.entries(user_access)) {
    if (user_id !== owner_id && access) shares.push({ user_id, access })
  }
  return shares
}
