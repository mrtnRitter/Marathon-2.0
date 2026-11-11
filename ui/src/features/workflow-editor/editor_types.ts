import { createContext } from 'react'
import type { Edge, Node } from '@xyflow/react'
import type { ModuleType, WorkflowModule } from '../../data/types'

export type EditorMode = 'monitor' | 'edit'

export type ModuleNodeData = { module: WorkflowModule; module_type: ModuleType }
export type ModuleFlowNode = Node<ModuleNodeData, 'module'>

export const EditorModeContext = createContext<EditorMode>('monitor')
export const EditorActionsContext = createContext<{ toggle_lock: (module_id: string) => void } | null>(null)

export const drag_mime = 'application/x-marathon-module-type'

export type { Edge }
