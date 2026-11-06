import type { Worker } from '../types'
import { minutes_ago } from './random'

export const workers: Worker[] = [
  { id: 'w1', name: 'RENDER-01', status: 'busy', capabilities: ['transcode', 'move_files', 'wait'], max_parallel: 4, running_jobs: 3, client_version: '2.0.0', last_seen: minutes_ago(0.1) },
  { id: 'w2', name: 'RENDER-02', status: 'ready', capabilities: ['transcode', 'wait'], max_parallel: 2, running_jobs: 0, client_version: '2.0.0', last_seen: minutes_ago(0.3) },
  { id: 'w3', name: 'NAS-MOVER', status: 'updating', capabilities: ['move_files', 'condition'], max_parallel: 8, running_jobs: 0, client_version: '1.9.4', last_seen: minutes_ago(1) },
  { id: 'w4', name: 'EDIT-SUITE-3', status: 'new', capabilities: [], max_parallel: 1, running_jobs: 0, client_version: '2.0.0', last_seen: minutes_ago(4) },
  { id: 'w5', name: 'LAPTOP-MR', status: 'offline', capabilities: ['wait'], max_parallel: 1, running_jobs: 0, client_version: '2.0.0', last_seen: minutes_ago(2900) },
]
