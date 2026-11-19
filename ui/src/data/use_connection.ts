import { useSyncExternalStore } from 'react'
import { live_connected, subscribe_connection } from './live'

export function use_server_connected(): boolean {
  return useSyncExternalStore(subscribe_connection, live_connected, live_connected)
}
