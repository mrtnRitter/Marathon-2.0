import { useContext, useEffect, useRef } from 'react'
import { Handle, Position, useConnection, useUpdateNodeInternals, type NodeProps } from '@xyflow/react'
import { Icon } from '../../components/Icon'
import { module_counter_meta, output_color } from '../../theme/status'
import { EditorActionsContext, EditorModeContext, type ModuleFlowNode } from './editor_types'

const short_label = { discovered: 'erfasst', waiting: 'wartet', running: 'läuft', done: 'erledigt', failed: 'Fehler' }
const blind_hint = 'Dieses Modul ist für Dich nicht zur Ansicht freigegeben'
const blind_name = 'Nicht freigegebenes Modul'

/** Module box: input on top, labeled outputs at the bottom, live counters in monitor mode. */
export function ModuleNode({ data, selected }: NodeProps<ModuleFlowNode>) {
  const mode = useContext(EditorModeContext)
  const actions = useContext(EditorActionsContext)
  const connection_in_progress = useConnection(connection => connection.inProgress)
  const update_node_internals = useUpdateNodeInternals()
  const previous_connection_state = useRef(connection_in_progress)
  const { module, module_type } = data
  const blind = module.blind === true
  const counter_keys = blind && module.counters.discovered > 0
    ? ['discovered', 'waiting', 'running', 'done', 'failed'] as const
    : module_type.id === 'wait' ? ['running', 'done', 'failed'] as const : module_type.id === 'watchfolder' ? ['discovered', 'waiting'] as const : ['waiting', 'running', 'done', 'failed'] as const
  const description = blind ? '' : module.description?.trim() ?? ''
  const running = module.counters.running > 0
  const lock_icon = <Icon name="lock" size={13} />
  useEffect(() => {
    if (previous_connection_state.current === connection_in_progress) return
    previous_connection_state.current = connection_in_progress
    update_node_internals(data.module.id)
  }, [connection_in_progress, data.module.id, update_node_internals])
  return (
    <div className={`module-node${selected ? ' selected' : ''}${mode === 'monitor' && running ? ' running' : ''}${module.locked ? ' locked' : ''}`}>
      {mode === 'edit' ? (
        <button
          type="button"
          className={`module-node-lock nodrag nopan${module.locked ? ' locked' : ''}`}
          title={module.locked ? 'Entsperren' : 'Sperren'}
          aria-label={module.locked ? 'Entsperren' : 'Sperren'}
          onPointerDown={event => event.stopPropagation()}
          onMouseDown={event => event.stopPropagation()}
          onClick={event => { event.stopPropagation(); actions?.toggle_lock(module.id) }}
        >
          <Icon name={module.locked ? 'lock' : 'lock-open'} size={13} />
        </button>
      ) : module.locked ? (
        <span className="module-node-lock locked" title="Gesperrt" aria-label="Gesperrt">{lock_icon}</span>
      ) : null}
      {module_type.has_input && <Handle type="target" id="in" position={Position.Top} className={`module-handle module-input-handle${connection_in_progress ? ' connecting' : ''}`} isConnectable={mode === 'edit' && !module.locked} isConnectableStart={false} isConnectableEnd={mode === 'edit' && !module.locked} />}
      <div className="module-node-head">
        {!blind && <span className="module-node-icon"><Icon name={module_type.icon} size={16} /></span>}
        <div className="module-node-title">
          <div className="module-node-name" title={blind ? blind_hint : module.name}>{blind ? blind_name : module.name}{module.update_available && <span className="issue-mark" title="Neue Version verfügbar" aria-label="Neue Version verfügbar"><Icon name="alert" size={14} /></span>}</div>
          {!blind && <div className="module-node-type">{module_type.name}</div>}
          {!blind && description && <div className="module-node-desc" title={description}>{description}</div>}
        </div>
      </div>
      {mode === 'monitor' && (
        <div className="module-node-counters" style={{ gridTemplateColumns: `repeat(${counter_keys.length}, 1fr)` }}>
          {counter_keys.map(key => (
            <div key={key} className="module-counter" style={{ color: module_counter_meta[key].color }} title={module_counter_meta[key].label}>
              <b>{module.counters[key]}</b>
              <span>{short_label[key]}</span>
            </div>
          ))}
        </div>
      )}
      <div className="module-node-outputs">
        {module_type.outputs.map(output => (
          <div key={output.id} className="module-output" style={{ color: output_color(output.id) }} data-tip={output.description}>
            <span>{output.label}</span>
            <Handle type="source" id={output.id} position={Position.Bottom} className={`module-handle module-output-handle${connection_in_progress ? ' connecting' : ''}`} isConnectable={mode === 'edit' && !module.locked} isConnectableStart={mode === 'edit' && !module.locked} isConnectableEnd={false} />
          </div>
        ))}
      </div>
    </div>
  )
}
