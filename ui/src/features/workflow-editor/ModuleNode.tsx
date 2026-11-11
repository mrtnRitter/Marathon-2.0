import { useContext } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Icon } from '../../components/Icon'
import { module_counter_meta, output_color } from '../../theme/status'
import { EditorActionsContext, EditorModeContext, type ModuleFlowNode } from './editor_types'

const counter_keys = ['waiting', 'running', 'done', 'failed'] as const
const short_label = { waiting: 'wartet', running: 'läuft', done: 'erledigt', failed: 'Fehler' }

/** Module box: input on top, labeled outputs at the bottom, live counters in monitor mode. */
export function ModuleNode({ data, selected }: NodeProps<ModuleFlowNode>) {
  const mode = useContext(EditorModeContext)
  const actions = useContext(EditorActionsContext)
  const { module, module_type } = data
  const running = module.counters.running > 0
  const lock_icon = <Icon name="lock" size={13} />
  return (
    <div className={`module-node${selected ? ' selected' : ''}${mode === 'monitor' && running ? ' running' : ''}${module.locked ? ' locked' : ''}`}>
      {mode === 'edit' ? (
        <button
          type="button"
          className={`module-node-lock nodrag nopan${module.locked ? ' locked' : ''}`}
          title={module.locked ? 'Entsperren' : 'Sperren'}
          aria-label={module.locked ? 'Entsperren' : 'Sperren'}
          onClick={event => { event.stopPropagation(); actions?.toggle_lock(module.id) }}
        >
          <Icon name={module.locked ? 'lock' : 'lock-open'} size={13} />
        </button>
      ) : module.locked ? (
        <span className="module-node-lock locked" title="Gesperrt" aria-label="Gesperrt">{lock_icon}</span>
      ) : null}
      {module_type.has_input && <Handle type="target" id="in" position={Position.Top} className="module-handle" isConnectable={mode === 'edit' && !module.locked} />}
      <div className="module-node-head">
        <span className="module-node-icon"><Icon name={module_type.icon} size={16} /></span>
        <div className="module-node-title">
          <div className="module-node-name">{module.name}</div>
          <div className="module-node-type">{module_type.name}</div>
        </div>
      </div>
      {mode === 'monitor' && (
        <div className="module-node-counters">
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
            <Handle type="source" id={output.id} position={Position.Bottom} className="module-handle" isConnectable={mode === 'edit' && !module.locked} />
          </div>
        ))}
      </div>
    </div>
  )
}
