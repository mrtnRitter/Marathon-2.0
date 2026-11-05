import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  addEdge,
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './styles.css';

const statuses = ['queued', 'running', 'paused', 'completed', 'failed'];
const statusNames = {
  queued: 'Ausstehend', running: 'In Arbeit', paused: 'Pausiert',
  completed: 'Abgeschlossen', failed: 'Fehlgeschlagen',
};
const navItems = [
  { id: 'dashboard', label: 'Dashboard', glyph: '▦' },
  { id: 'workflows', label: 'Workflows', glyph: '⌘' },
  { id: 'assets', label: 'Assets', glyph: '▤' },
  { id: 'workers', label: 'Worker', glyph: '◉' },
];

async function api(path, options = {}) {
  const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers };
  const response = await fetch(`/api${path}`, { ...options, headers });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.error || `Anfrage fehlgeschlagen (${response.status})`);
  return body;
}

function labelStatus(status) {
  return statusNames[status] || status || 'Unbekannt';
}

function getSettingsFields(description) {
  const schema = description?.settings;
  let fields = [];
  if (Array.isArray(schema)) fields = schema;
  else if (Array.isArray(schema?.fields)) fields = schema.fields;
  else if (schema?.properties && typeof schema.properties === 'object') {
    fields = Object.entries(schema.properties).map(([name, value]) => ({ name, ...value }));
  } else if (schema && typeof schema === 'object') {
    fields = Object.entries(schema).map(([name, value]) => ({ name, ...(typeof value === 'object' ? value : { default: value }) }));
  }
  if (fields.length) return fields;
  if (description?.type === 'wait' || description?.name === 'Warten') {
    return [
      { name: 'seconds', label: 'Dauer (Sekunden)', type: 'number', default: 1, minimum: 0 },
      { name: 'fail', label: 'Fehlerausgang', type: 'boolean', default: false },
    ];
  }
  return [];
}

function App() {
  const [page, setPage] = useState('dashboard');
  const [dashboard, setDashboard] = useState({ workflows: [], workers: [] });
  const [workflows, setWorkflows] = useState([]);
  const [moduleTypes, setModuleTypes] = useState([]);
  const [assets, setAssets] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [rollouts, setRollouts] = useState({ packages: [], workers: [] });
  const [serverVersion, setServerVersion] = useState('');
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [selectedAsset, setSelectedAsset] = useState(null);
  const [assetFilter, setAssetFilter] = useState('all');
  const [revision, setRevision] = useState(0);
  const [connection, setConnection] = useState('connecting');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [dash, flowData, typesData, assetData, workerData, rolloutData, health] = await Promise.all([
        api('/dashboard'), api('/workflows'), api('/module-types'), api('/assets'),
        api('/workers'), api('/rollouts'), api('/health'),
      ]);
      setDashboard(dash || { workflows: [], workers: [] });
      setWorkflows(flowData?.items || []);
      setModuleTypes(typesData?.items || []);
      setAssets(assetData?.items || []);
      setWorkers(workerData?.items || []);
      setRollouts(rolloutData || { packages: [], workers: [] });
      setServerVersion(health?.version || '');
      setError('');
    } catch (err) {
      setError(err.message || 'Die Verbindung zum Server ist fehlgeschlagen.');
    }
  }, []);

  useEffect(() => {
    refresh();
    const stream = new EventSource('/api/events');
    stream.onopen = () => setConnection('live');
    stream.onerror = () => setConnection('reconnecting');
    const eventTypes = ['workflow.updated', 'asset.updated', 'job.updated', 'worker.updated', 'rollout.updated'];
    eventTypes.forEach((type) => stream.addEventListener(type, () => {
      setRevision((value) => value + 1);
      refresh();
    }));
    return () => stream.close();
  }, [refresh]);

  const runAction = async (operation, successMessage = '') => {
    setBusy(true);
    try {
      await operation();
      await refresh();
      if (successMessage) setError('');
    } catch (err) {
      setError(err.message || 'Aktion fehlgeschlagen.');
    } finally {
      setBusy(false);
    }
  };

  const openWorkflow = (workflow) => {
    setSelectedWorkflow(workflow);
    setPage('workflow-detail');
  };
  const openAsset = (asset) => {
    setSelectedAsset(asset);
  };
  const currentWorkflow = workflows.find((item) => item.id === selectedWorkflow?.id) || selectedWorkflow;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">M</span><span>MARATHON<span className="brand-dot">.</span></span></div>
        <div className="workspace-label">CONTROL ROOM</div>
        <nav aria-label="Hauptnavigation">
          {navItems.map((item) => (
            <button key={item.id} className={`nav-item ${page === item.id || (item.id === 'workflows' && page === 'workflow-detail') ? 'active' : ''}`} onClick={() => { setPage(item.id); setSelectedAsset(null); }}>
              <span className="nav-glyph">{item.glyph}</span>{item.label}
              {item.id === 'workflows' && <span className="nav-count">{workflows.length}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="live-badge"><span className={`pulse ${connection}`} />{connection === 'live' ? 'Live verbunden' : connection === 'reconnecting' ? 'Verbindung wird erneuert' : 'Verbinde …'}</div>
          <div className="server-caption">{serverVersion ? `Server ${serverVersion}` : 'Lokaler Server'}</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>MARATHON</span><span className="crumb-separator">/</span><strong>{page === 'workflow-detail' ? (currentWorkflow?.name || 'Workflow') : navItems.find((item) => item.id === page)?.label || 'Dashboard'}</strong></div>
          <div className="topbar-right"><span className={`connection-label ${connection}`}>● {connection === 'live' ? 'LIVE' : connection === 'reconnecting' ? 'RECONNECTING' : 'CONNECTING'}</span><span className="user-mark">MR</span></div>
        </header>

        {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError('')} aria-label="Meldung schließen">×</button></div>}

        {page === 'dashboard' && <Dashboard dashboard={dashboard} workflows={workflows} assets={assets} onWorkflow={openWorkflow} onPage={setPage} onFilter={(filter) => { setAssetFilter(filter); setPage('assets'); }} />}
        {page === 'workflows' && <WorkflowList workflows={workflows} onOpen={openWorkflow} onCreate={() => { setSelectedWorkflow(null); setPage('workflow-detail'); }} onAction={runAction} busy={busy} />}
        {page === 'workflow-detail' && <WorkflowScreen key={currentWorkflow?.id || 'new'} workflow={currentWorkflow} moduleTypes={moduleTypes} assets={assets} workers={workers} revision={revision} onBack={() => setPage('workflows')} onSave={async (payload) => {
          setBusy(true);
          try {
            const saved = currentWorkflow?.id
              ? await api(`/workflows/${encodeURIComponent(currentWorkflow.id)}`, { method: 'PUT', body: JSON.stringify(payload) })
              : await api('/workflows', { method: 'POST', body: JSON.stringify(payload) });
            await refresh();
            setSelectedWorkflow(saved);
            setPage('workflow-detail');
          } catch (err) { setError(err.message); }
          finally { setBusy(false); }
        }} onAction={runAction} busy={busy} />}
        {page === 'assets' && <AssetsView assets={assets} workflows={workflows} filter={assetFilter} setFilter={setAssetFilter} onOpen={openAsset} onAction={runAction} busy={busy} />}
        {page === 'workers' && <WorkersView workers={workers} rollouts={rollouts} onAction={runAction} busy={busy} />}
      </main>

      {selectedAsset && <AssetDetail assetId={selectedAsset.id} revision={revision} onClose={() => setSelectedAsset(null)} onAction={runAction} busy={busy} />}
    </div>
  );
}

function PageHeading({ eyebrow, title, description, action }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{action && <div className="heading-action">{action}</div>}</div>;
}

function Dashboard({ dashboard, workflows, assets, onWorkflow, onPage, onFilter }) {
  const totals = useMemo(() => Object.fromEntries(statuses.map((status) => [status, assets.filter((asset) => asset.status === status).length])), [assets]);
  const cards = [
    ['Aktive Workflows', workflows.filter((workflow) => workflow.enabled).length, '↗'],
    ['Assets gesamt', assets.length, '▤'],
    ['Worker online', (dashboard.workers || []).filter((worker) => worker.status === 'online').length, '◉'],
    ['Fehlgeschlagen', totals.failed, '!'],
  ];
  return <section className="page-content">
    <PageHeading eyebrow="ÜBERSICHT / LIVE STATUS" title="Dashboard" description="Ein aktueller Blick auf Workflows, Assets und Worker." action={<span className="live-inline"><i /> Echtzeit aktiv</span>} />
    <div className="metric-grid">{cards.map(([label, value, glyph]) => <div className="metric-card" key={label}><div className="metric-top"><span>{label}</span><span className="metric-glyph">{glyph}</span></div><strong>{value}</strong><span className="metric-foot">Aktueller Stand</span></div>)}</div>
    <div className="section-title-row"><div><div className="eyebrow">PIPELINES</div><h2>Workflow-Übersicht</h2></div><button className="text-button" onClick={() => onPage('workflows')}>Alle Workflows <span>→</span></button></div>
    <div className="panel workflow-table-panel">
      <div className="table-head workflow-grid"><span>WORKFLOW</span><span>STATUS</span>{statuses.map((status) => <span key={status}>{statusNames[status].toUpperCase()}</span>)}<span>VERSION</span></div>
      {(dashboard.workflows?.length ? dashboard.workflows : workflows).map((workflow) => {
        const counts = workflow.asset_counts || {};
        return <div className="table-row workflow-grid" key={workflow.id}>
          <button className="workflow-name" onClick={() => onWorkflow(workflows.find((item) => item.id === workflow.id) || workflow)}><span className="workflow-symbol">⌘</span><span>{workflow.name}</span><span className="row-arrow">↗</span></button>
          <span><StatusPill status={workflow.enabled ? 'running' : 'paused'} label={workflow.enabled ? 'Aktiv' : 'Pausiert'} /></span>
          {statuses.map((status) => <button key={status} className="count-cell" onClick={() => onFilter(status)}>{counts[status] ?? assets.filter((asset) => asset.workflow_id === workflow.id && asset.status === status).length}</button>)}
          <span className="version-cell">v{workflow.version || '—'}</span>
        </div>;
      })}
      {!workflows.length && <EmptyState title="Noch keine Workflows" text="Erstelle einen Workflow, um die Verarbeitung einzurichten." />}
    </div>
    <div className="section-title-row compact"><div><div className="eyebrow">SYSTEM</div><h2>Gesamtstatus</h2></div><button className="text-button" onClick={() => onPage('assets')}>Assets öffnen <span>→</span></button></div>
    <div className="status-overview">{statuses.map((status) => <button className="status-summary panel" key={status} onClick={() => onFilter(status)}><span className={`status-dot ${status}`} /><span>{statusNames[status]}</span><strong>{totals[status]}</strong></button>)}</div>
  </section>;
}

function WorkflowList({ workflows, onOpen, onCreate, onAction, busy }) {
  return <section className="page-content">
    <PageHeading eyebrow="ORCHESTRATION" title="Workflows" description="Erstelle Verarbeitungspipelines und überwache ihre Ausführung." action={<button className="primary-button" onClick={onCreate}>＋ Neuer Workflow</button>} />
    <div className="workflow-cards">{workflows.map((workflow) => <article className="panel workflow-card" key={workflow.id}>
      <button className="card-main-action" onClick={() => onOpen(workflow)}><span className="workflow-symbol large">⌘</span><span className="workflow-card-copy"><strong>{workflow.name}</strong><small>Version {workflow.version || '—'} · aktualisiert {formatDate(workflow.updated_at)}</small></span><span className="row-arrow">↗</span></button>
      <div className="workflow-card-bottom"><StatusPill status={workflow.enabled ? 'running' : 'paused'} label={workflow.enabled ? 'Aktiv' : 'Pausiert'} /><button className="secondary-button small" disabled={busy} onClick={() => onAction(() => api(`/workflows/${encodeURIComponent(workflow.id)}/actions`, { method: 'POST', body: JSON.stringify({ action: workflow.enabled ? 'pause' : 'resume' }) }))}>{workflow.enabled ? 'Pausieren' : 'Aktivieren'}</button></div>
    </article>)}
    {!workflows.length && <EmptyState title="Noch keine Workflows" text="Beginne mit einem leeren Graphen und füge Module hinzu." action={<button className="primary-button" onClick={onCreate}>Workflow erstellen</button>} />}</div>
  </section>;
}

function WorkflowScreen({ workflow, moduleTypes, assets, workers, revision, onBack, onSave, onAction, busy }) {
  const [mode, setMode] = useState(workflow ? 'monitor' : 'edit');
  const [jobs, setJobs] = useState([]);
  const [jobsError, setJobsError] = useState('');
  const [selectedModule, setSelectedModule] = useState(null);
  useEffect(() => {
    if (!workflow?.id) return;
    api(`/jobs?workflow_id=${encodeURIComponent(workflow.id)}`).then((data) => { setJobs(data?.items || []); setJobsError(''); }).catch((err) => setJobsError(err.message));
  }, [workflow?.id, revision]);
  const graphNodes = workflow?.graph?.nodes || [];
  const moduleNodes = graphNodes.filter((node) => node.id !== 'start');
  const counts = (nodeId) => jobs.filter((job) => job.module_id === nodeId).reduce((result, job) => { result[job.status] = (result[job.status] || 0) + 1; return result; }, {});

  return <section className="page-content workflow-page">
    <div className="back-line"><button className="text-button" onClick={onBack}>← Workflows</button><span>/</span><span>{workflow?.name || 'Neuer Workflow'}</span></div>
    <PageHeading eyebrow="PIPELINE DESIGNER" title={workflow?.name || 'Workflow erstellen'} description={workflow ? `Version ${workflow.version || '1'} · ${workflow.enabled ? 'Verarbeitung aktiv' : 'Verarbeitung pausiert'}` : 'Lege einen Namen fest und baue deinen ersten Workflow.'} action={<div className="mode-switch"><button className={mode === 'edit' ? 'selected' : ''} onClick={() => setMode('edit')}>Bearbeiten</button><button className={mode === 'monitor' ? 'selected' : ''} onClick={() => setMode('monitor')}>Überwachen</button></div>} />
    {workflow && <div className="workflow-toolbar"><span className="workflow-toolbar-status"><StatusPill status={workflow.enabled ? 'running' : 'paused'} label={workflow.enabled ? 'Aktiv' : 'Pausiert'} /> <span>{assets.filter((asset) => asset.workflow_id === workflow.id).length} Assets</span></span><button className="secondary-button" disabled={busy} onClick={() => onAction(() => api(`/workflows/${encodeURIComponent(workflow.id)}/actions`, { method: 'POST', body: JSON.stringify({ action: workflow.enabled ? 'pause' : 'resume' }) }))}>{workflow.enabled ? 'Workflow pausieren' : 'Workflow aktivieren'}</button></div>}
    {mode === 'edit' ? <WorkflowEditor workflow={workflow} moduleTypes={moduleTypes} onSave={onSave} busy={busy} /> : <div className="monitor-layout">
      <div className="panel monitor-canvas">
        <div className="panel-heading"><div><h2>Modulstatus</h2><p>Live-Zähler je Workflow-Modul</p></div><span className="live-inline"><i /> LIVE</span></div>
        {jobsError && <div className="inline-error">{jobsError}</div>}
        <div className="monitor-flow"><div className="monitor-node start-node"><span>▶</span><strong>Start</strong><small>Server</small></div>{moduleNodes.map((node, index) => {
          const typeInfo = moduleTypes.find((item) => item.type === node.type);
          const moduleCounts = counts(node.id);
          const isSelected = selectedModule === node.id;
          return <React.Fragment key={node.id}><div className="monitor-connector" /><button className={`monitor-node ${isSelected ? 'chosen' : ''}`} onClick={() => setSelectedModule(node.id)}><span className="node-icon">{String(index + 1).padStart(2, '0')}</span><strong>{typeInfo?.name || node.type}</strong><small>{moduleCounts.running || 0} aktiv · {moduleCounts.completed || 0} fertig</small><div className="node-counts">{['queued', 'running', 'failed'].map((status) => <span key={status} title={statusNames[status]}>{moduleCounts[status] || 0}</span>)}</div></button></React.Fragment>;
        })}{!moduleNodes.length && <div className="empty-flow">Dieser Workflow enthält noch keine Module. Öffne <button onClick={() => setMode('edit')}>Bearbeiten</button>, um ihn aufzubauen.</div>}</div>
      </div>
      {selectedModule && <ModuleJobs moduleId={selectedModule} jobs={jobs.filter((job) => job.module_id === selectedModule)} assets={assets} workers={workers} onClose={() => setSelectedModule(null)} />}
    </div>}
  </section>;
}

function WorkflowEditor({ workflow, moduleTypes, onSave, busy }) {
  const flowRef = useRef(null);
  const graphNodes = workflow?.graph?.nodes || [];
  const normalizedNodes = graphNodes.some((node) => node.id === 'start') ? graphNodes : [{ id: 'start', type: 'start', settings: {} }, ...graphNodes];
  const initialNodes = normalizedNodes.length
    ? normalizedNodes.map((node, index) => ({ id: node.id, type: 'default', position: node.id === 'start' ? { x: 80, y: 150 } : { x: 310 + index * 220, y: 150 }, data: { label: node.id === 'start' ? 'Start · Server' : moduleTypes.find((item) => item.type === node.type)?.name || node.type, type: node.type, settings: node.settings || {} }, draggable: node.id !== 'start' }))
    : [{ id: 'start', type: 'default', position: { x: 80, y: 150 }, data: { label: 'Start · Server', type: 'start', settings: {} }, draggable: false }];
  const initialEdges = (workflow?.graph?.edges || []).map((edge, index) => ({ id: `edge-${edge.from}-${edge.to}-${index}`, source: edge.from, target: edge.to, animated: false }));
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [name, setName] = useState(workflow?.name || '');
  const [formError, setFormError] = useState('');
  const selectedNode = nodes.find((node) => node.id === selectedNodeId && node.id !== 'start');
  const selectedType = moduleTypes.find((item) => item.type === selectedNode?.data.type);
  const fields = getSettingsFields(selectedType);
  const onConnect = useCallback((connection) => setEdges((current) => addEdge({ ...connection, animated: true }, current)), [setEdges]);

  const dropModule = (event) => {
    event.preventDefault();
    const type = event.dataTransfer.getData('application/marathon-module');
    if (!type || !flowRef.current) return;
    const description = moduleTypes.find((item) => item.type === type);
    const position = flowRef.current.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    const id = `node-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const defaults = Object.fromEntries(getSettingsFields(description).map((field) => [field.name || field.key, field.default ?? field.defaultValue ?? '']));
    setNodes((current) => [...current, { id, type: 'default', position, data: { label: description?.name || type, type, settings: defaults } }]);
    setSelectedNodeId(id);
  };

  const save = () => {
    if (!name.trim()) { setFormError('Bitte gib dem Workflow einen Namen.'); return; }
    const graph = {
      nodes: nodes.map((node) => ({ id: node.id, type: node.data.type, settings: node.data.settings || {} })),
      edges: edges.map((edge) => ({ from: edge.source, to: edge.target })),
    };
    setFormError('');
    onSave({ name: name.trim(), graph, ...(workflow ? {} : { enabled: false }) });
  };

  return <div className="editor-layout">
    <aside className="panel module-palette"><div className="panel-heading"><div><h2>Modultypen</h2><p>Auf die Fläche ziehen</p></div></div><div className="palette-list">{moduleTypes.map((item) => <button key={item.type} className="palette-item" draggable onDragStart={(event) => { event.dataTransfer.setData('application/marathon-module', item.type); event.dataTransfer.effectAllowed = 'move'; }}><span className="node-icon">＋</span><span><strong>{item.name || item.type}</strong><small>{item.description || item.execution || 'Verarbeitungsmodul'}</small></span><span className="drag-grip">⠿</span></button>)}{!moduleTypes.length && <div className="subtle-note">Modultypen konnten nicht geladen werden.</div>}</div><div className="start-note"><span>▶</span><div><strong>Start</strong><small>Fester Einstieg · läuft auf dem Server</small></div></div></aside>
    <div className="editor-main">
      <div className="editor-toolbar panel"><label className="workflow-name-input"><span>WORKFLOW-NAME</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="z. B. Ingest Pipeline" /></label><div className="editor-actions">{formError && <span className="form-error">{formError}</span>}<button className="primary-button" disabled={busy} onClick={save}>{busy ? 'Speichere …' : 'Workflow speichern'}</button></div></div>
      <div className="flow-wrap panel" onDrop={dropModule} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; }}>
        <ReactFlow onInit={(instance) => { flowRef.current = instance; }} nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} onNodeClick={(_, node) => node.id !== 'start' && setSelectedNodeId(node.id)} onPaneClick={() => setSelectedNodeId(null)} fitView fitViewOptions={{ padding: 0.2 }} minZoom={0.25} maxZoom={1.5}>
          <Background color="#29333d" gap={22} size={1} /><Controls /><MiniMap nodeColor={(node) => node.id === 'start' ? '#36d6ad' : '#697f96'} maskColor="rgba(7, 11, 15, 0.72)" />
        </ReactFlow>
        {!nodes.some((node) => node.id !== 'start') && <div className="canvas-hint">Ziehe ein Modul aus der linken Liste auf die Fläche und verbinde es mit Start.</div>}
      </div>
      {selectedNode && <div className="settings-panel panel"><div className="panel-heading"><div><div className="eyebrow">MODUL-EINSTELLUNGEN</div><h2>{selectedNode.data.label}</h2></div><button className="icon-button" onClick={() => setSelectedNodeId(null)}>×</button></div><p className="settings-description">{selectedType?.description || 'Einstellungen für dieses Modul.'}</p>
        {fields.length ? fields.map((field) => <SettingField key={field.name || field.key} field={field} value={selectedNode.data.settings?.[field.name || field.key]} onChange={(value) => setNodes((current) => current.map((node) => node.id === selectedNode.id ? { ...node, data: { ...node.data, settings: { ...node.data.settings, [field.name || field.key]: value } } } : node))} />) : <div className="subtle-note">Für diesen Modultyp sind keine Formfelder beschrieben.</div>}
        <button className="danger-button" onClick={() => { setNodes((current) => current.filter((node) => node.id !== selectedNode.id)); setEdges((current) => current.filter((edge) => edge.source !== selectedNode.id && edge.target !== selectedNode.id)); setSelectedNodeId(null); }}>Modul entfernen</button>
      </div>}
    </div>
  </div>;
}

function SettingField({ field, value, onChange }) {
  const name = field.name || field.key;
  const label = field.label || field.title || name;
  const type = field.type === 'boolean' ? 'boolean' : field.type || 'text';
  if (type === 'boolean') return <label className="setting-toggle"><span>{label}<small>{field.description || ''}</small></span><input type="checkbox" checked={Boolean(value ?? field.default ?? false)} onChange={(event) => onChange(event.target.checked)} /></label>;
  if (Array.isArray(field.enum) || Array.isArray(field.options)) return <label className="field-label">{label}<select value={value ?? field.default ?? ''} onChange={(event) => onChange(event.target.value)}>{(field.enum || field.options).map((option) => <option key={typeof option === 'object' ? option.value : option} value={typeof option === 'object' ? option.value : option}>{typeof option === 'object' ? option.label || option.value : option}</option>)}</select></label>;
  return <label className="field-label">{label}<input type={['number', 'integer'].includes(type) ? 'number' : 'text'} min={field.minimum} max={field.maximum} step={type === 'integer' ? 1 : 'any'} value={value ?? field.default ?? ''} onChange={(event) => onChange(['number', 'integer'].includes(type) ? Number(event.target.value) : event.target.value)} />{field.description && <small>{field.description}</small>}</label>;
}

function ModuleJobs({ moduleId, jobs, assets, workers, onClose }) {
  const assetById = Object.fromEntries(assets.map((asset) => [asset.id, asset]));
  const workerById = Object.fromEntries(workers.map((worker) => [worker.id, worker]));
  return <aside className="panel module-jobs"><div className="panel-heading"><div><div className="eyebrow">MODUL-LÄUFE</div><h2>Ausführung</h2></div><button className="icon-button" onClick={onClose}>×</button></div><p className="subtle-note">Modul {moduleId}</p>{jobs.length ? jobs.slice().reverse().map((job) => <article className="job-card" key={job.id}><div className="job-card-top"><StatusPill status={job.status} /><span className="job-attempt">Versuch {job.attempt || 1}</span></div><div className="job-meta"><strong>Asset</strong><span>{(job.asset_ids || []).map((id) => assetById[id]?.name || id).join(', ') || '—'}</span><strong>Worker</strong><span>{workerById[job.worker_id]?.name || job.worker_id || 'Nicht zugewiesen'}</span></div>{normalizeLogs(job.logs).length > 0 && <LogEntries entries={job.logs} />}</article>) : <EmptyState title="Noch keine Jobs" text="Für dieses Modul gibt es noch keine Ausführungen." />}</aside>;
}

function AssetsView({ assets, workflows, filter, setFilter, onOpen, onAction, busy }) {
  const [workflowId, setWorkflowId] = useState(workflows[0]?.id || '');
  const [assetName, setAssetName] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [importMessage, setImportMessage] = useState('');
  const visible = filter === 'all' ? assets : assets.filter((asset) => asset.status === filter);
  const addAsset = async () => {
    if (!assetName.trim() || !workflowId) return;
    await onAction(() => api('/assets', { method: 'POST', body: JSON.stringify({ workflow_id: workflowId, name: assetName.trim(), metadata: {} }) }));
    setAssetName(''); setShowAdd(false);
  };
  const importCsv = async (event) => {
    const file = event.target.files?.[0];
    if (!file || !workflowId) return;
    const csv = await file.text();
    try {
      const result = await api(`/assets/import?workflow_id=${encodeURIComponent(workflowId)}`, { method: 'POST', headers: { 'Content-Type': 'text/csv; charset=utf-8' }, body: csv });
      setImportMessage(`${result?.items?.length ?? 0} Assets importiert.`);
      await onAction(async () => {});
    } catch (err) { setImportMessage(err.message); }
    event.target.value = '';
  };
  return <section className="page-content">
    <PageHeading eyebrow="INPUT / OUTPUT" title="Assets" description="Verwalte Verarbeitungseingänge, Status und Asset-Metadaten." action={<button className="primary-button" onClick={() => setShowAdd((value) => !value)}>＋ Asset hinzufügen</button>} />
    <div className="assets-toolbar panel"><label className="field-label workflow-select">Workflow<select value={workflowId} onChange={(event) => setWorkflowId(event.target.value)}>{workflows.map((workflow) => <option key={workflow.id} value={workflow.id}>{workflow.name}</option>)}</select></label><label className="import-button secondary-button">CSV importieren<input type="file" accept=".csv,text/csv" onChange={importCsv} disabled={!workflowId || busy} /></label>{importMessage && <span className="import-message">{importMessage}</span>}</div>
    {showAdd && <div className="add-asset-panel panel"><label className="field-label">Asset-Name<input value={assetName} onChange={(event) => setAssetName(event.target.value)} placeholder="z. B. shot_014.exr" onKeyDown={(event) => event.key === 'Enter' && addAsset()} /></label><button className="primary-button" disabled={busy || !assetName.trim() || !workflowId} onClick={addAsset}>Hinzufügen</button></div>}
    <div className="status-tabs">{[{ id: 'all', label: 'Alle' }, ...statuses.map((status) => ({ id: status, label: statusNames[status] }))].map((tab) => <button key={tab.id} className={filter === tab.id ? 'selected' : ''} onClick={() => setFilter(tab.id)}>{tab.label}<span>{tab.id === 'all' ? assets.length : assets.filter((asset) => asset.status === tab.id).length}</span></button>)}</div>
    <div className="panel asset-list"><div className="table-head asset-grid"><span>ASSET</span><span>WORKFLOW</span><span>STATUS</span><span>AKTUELLES MODUL</span><span>ERSTELLT</span><span>AKTIONEN</span></div>{visible.map((asset) => <AssetRow key={asset.id} asset={asset} workflows={workflows} onOpen={onOpen} onAction={onAction} busy={busy} />)}{!visible.length && <EmptyState title="Keine Assets in dieser Ansicht" text="Füge ein Asset hinzu oder passe den Statusfilter an." />}</div>
  </section>;
}

function AssetRow({ asset, workflows, onOpen, onAction, busy }) {
  const workflow = workflows.find((item) => item.id === asset.workflow_id);
  const action = asset.status === 'failed' || asset.status === 'completed' ? 'retry' : asset.paused || asset.status === 'paused' ? 'resume' : 'pause';
  return <div className="table-row asset-grid"><button className="asset-name" onClick={() => onOpen(asset)}><span className="asset-file-icon">▧</span><span>{asset.name}</span><span className="row-arrow">↗</span></button><span className="subtle-cell">{workflow?.name || '—'}</span><span><StatusPill status={asset.status} /></span><span className="subtle-cell">{asset.current_node || '—'}</span><span className="subtle-cell">{formatDate(asset.created_at)}</span><div className="row-actions"><button title={action === 'retry' ? 'Erneut verarbeiten' : action === 'resume' ? 'Freigeben' : 'Anhalten'} disabled={busy} onClick={() => onAction(() => api(`/assets/${encodeURIComponent(asset.id)}/actions`, { method: 'POST', body: JSON.stringify({ action }) }))}>{action === 'retry' ? '↻' : action === 'resume' ? '▶' : 'Ⅱ'}</button><button title="Entfernen" disabled={busy} onClick={() => onAction(() => api(`/assets/${encodeURIComponent(asset.id)}/actions`, { method: 'POST', body: JSON.stringify({ action: 'remove' }) }))}>×</button></div></div>;
}

function AssetDetail({ assetId, revision, onClose, onAction, busy }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { api(`/assets/${encodeURIComponent(assetId)}`).then((data) => { setDetail(data); setError(''); }).catch((err) => setError(err.message)); }, [assetId, revision]);
  const jobs = detail?.jobs || [];
  const history = detail?.history || [];
  return <div className="drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><aside className="asset-drawer">
    <div className="drawer-head"><div><div className="eyebrow">ASSET DETAIL</div><h2>{detail?.name || 'Asset'}</h2></div><button className="icon-button" onClick={onClose}>×</button></div>
    {error && <div className="inline-error">{error}</div>}{detail && <>
      <div className="detail-status-line"><StatusPill status={detail.status} /><span>Zuletzt aktualisiert {formatDate(detail.updated_at)}</span></div>
      <div className="detail-actions">{[['retry', 'Erneut verarbeiten'], [detail.paused || detail.status === 'paused' ? 'resume' : 'pause', detail.paused || detail.status === 'paused' ? 'Freigeben' : 'Anhalten'], ['remove', 'Entfernen']].map(([action, label]) => <button key={action} className={action === 'remove' ? 'danger-button' : 'secondary-button'} disabled={busy} onClick={() => onAction(() => api(`/assets/${encodeURIComponent(assetId)}/actions`, { method: 'POST', body: JSON.stringify({ action }) }))}>{label}</button>)}</div>
      <section className="detail-section"><h3>Metadaten</h3><div className="metadata-list"><div><span>Workflow</span><strong>{detail.workflow_id}</strong></div>{Object.entries(detail.metadata || {}).map(([key, value]) => <div key={key}><span>{key}</span><strong>{String(value)}</strong></div>)}{!Object.keys(detail.metadata || {}).length && <span className="subtle-note">Keine zusätzlichen Metadaten</span>}</div></section>
      <section className="detail-section"><h3>Zeitstrahl <span>{jobs.length} Jobs</span></h3><div className="timeline">{jobs.map((job) => <div className="timeline-item" key={job.id}><span className={`timeline-marker ${job.status}`} /><div><div className="timeline-title"><strong>{job.module_type || job.module_id}</strong><StatusPill status={job.status} /></div><p>Job {job.id} · Worker {job.worker_id || 'nicht zugewiesen'}</p><small>{formatDate(job.started_at || job.created_at)}{job.finished_at ? ` → ${formatDate(job.finished_at)}` : ''}{job.started_at && job.finished_at ? ` · ${duration(job.started_at, job.finished_at)}` : ''}</small></div></div>)}{!jobs.length && <span className="subtle-note">Noch keine Jobs erfasst.</span>}</div></section>
      <section className="detail-section"><h3>Meldungen &amp; Logs <span className="live-inline"><i /> LIVE</span></h3><LogEntries entries={[...history, ...jobs.flatMap((job) => normalizeLogs(job.logs).map((entry) => typeof entry === 'object' ? entry : { message: entry, created_at: job.updated_at }))]} /></section>
    </>}
  </aside></div>;
}

function WorkersView({ workers, rollouts, onAction, busy }) {
  const [selected, setSelected] = useState([]);
  const [version, setVersion] = useState('');
  const [target, setTarget] = useState('all');
  const packageItems = rollouts.packages || [];
  const rolloutWorkers = rollouts.workers || [];
  const versions = [...new Set([...packageItems.map((item) => typeof item === 'string' ? item : item.version), ...rolloutWorkers.map((item) => item.desired_version).filter(Boolean)])].filter(Boolean);
  useEffect(() => { if (!version && versions.length) setVersion(versions[versions.length - 1]); }, [versions.join('|'), version]);
  const toggleWorker = (id) => setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const rollout = () => onAction(() => api('/rollouts', { method: 'POST', body: JSON.stringify(target === 'all' ? { version } : { version, worker_ids: selected }) }));
  const renameWorker = (worker) => {
    const name = window.prompt('Neuer Worker-Name', worker.name || '');
    if (name?.trim()) onAction(() => api(`/workers/${encodeURIComponent(worker.id)}/actions`, { method: 'POST', body: JSON.stringify({ action: 'rename', name: name.trim() }) }));
  };
  return <section className="page-content">
    <PageHeading eyebrow="EXECUTION FLEET" title="Worker" description="Verwalte Worker-Freigaben, Kapazitäten und Client-Rollouts." action={<span className="live-inline"><i /> Status live</span>} />
    <div className="worker-metrics">{['online', 'new', 'blocked', 'offline'].map((status) => <div className="panel worker-metric" key={status}><span className={`status-dot ${status}`} /><span>{status === 'new' ? 'Neu' : status === 'blocked' ? 'Gesperrt' : status === 'online' ? 'Online' : 'Offline'}</span><strong>{workers.filter((worker) => worker.status === status).length}</strong></div>)}</div>
    <div className="section-title-row"><div><div className="eyebrow">FLEET</div><h2>Registrierte Worker</h2></div></div>
    <div className="panel worker-list"><div className="table-head worker-grid"><span></span><span>WORKER</span><span>STATUS</span><span>FÄHIGKEITEN</span><span>JOBS / MAX</span><span>CLIENT</span><span>AKTIONEN</span></div>{workers.map((worker) => <div className="table-row worker-grid" key={worker.id}><label className="checkbox-cell"><input type="checkbox" checked={selected.includes(worker.id)} onChange={() => toggleWorker(worker.id)} aria-label={`${worker.name} auswählen`} /></label><span className="worker-name"><span className="worker-avatar">{(worker.name || '?').slice(0, 1).toUpperCase()}</span><strong>{worker.name || 'Unbenannt'}</strong></span><span><StatusPill status={worker.status} /></span><span className="capability-list">{(worker.capabilities || []).map((capability) => <i key={capability}>{capability}</i>)}</span><span className="subtle-cell">{worker.active_jobs ?? 0} / {worker.max_parallel ?? '—'}</span><span className="subtle-cell">{worker.client_version || '—'}</span><div className="row-actions worker-actions">{worker.status === 'new' && <button disabled={busy} onClick={() => onAction(() => api(`/workers/${encodeURIComponent(worker.id)}/actions`, { method: 'POST', body: JSON.stringify({ action: 'approve' }) }))}>Freigeben</button>}{worker.status === 'blocked' ? <button disabled={busy} onClick={() => onAction(() => api(`/workers/${encodeURIComponent(worker.id)}/actions`, { method: 'POST', body: JSON.stringify({ action: 'unblock' }) }))}>Entsperren</button> : worker.status !== 'new' && <button disabled={busy} onClick={() => onAction(() => api(`/workers/${encodeURIComponent(worker.id)}/actions`, { method: 'POST', body: JSON.stringify({ action: 'block' }) }))}>Sperren</button>}<button title="Umbenennen" onClick={() => renameWorker(worker)}>✎</button></div></div>)}{!workers.length && <EmptyState title="Noch keine Worker" text="Worker erscheinen hier nach ihrer ersten Registrierung." />}</div>
    <div className="section-title-row compact"><div><div className="eyebrow">CLIENT MANAGEMENT</div><h2>Client-Version ausrollen</h2></div></div>
    <div className="rollout-panel panel"><div className="rollout-intro"><span className="rollout-icon">⇧</span><div><strong>Worker aktualisieren</strong><p>Das Update wird an alle oder ausgewählte Worker übergeben.</p></div></div><div className="rollout-controls"><label className="field-label">Client-Version<select value={version} onChange={(event) => setVersion(event.target.value)}>{versions.map((item) => <option value={item} key={item}>{item}</option>)}</select></label><div className="rollout-target"><span>Zielgruppe</span><div className="mode-switch"><button className={target === 'all' ? 'selected' : ''} onClick={() => setTarget('all')}>Alle Worker</button><button className={target === 'selected' ? 'selected' : ''} onClick={() => setTarget('selected')}>Ausgewählte ({selected.length})</button></div></div><button className="primary-button" disabled={busy || !version || (target === 'selected' && !selected.length)} onClick={rollout}>Rollout starten</button></div>{!versions.length && <div className="subtle-note">Keine Client-Versionen verfügbar.</div>}</div>
  </section>;
}

function StatusPill({ status, label }) {
  return <span className={`status-pill ${status || ''}`}><i />{label || labelStatus(status)}</span>;
}

function EmptyState({ title, text, action }) {
  return <div className="empty-state"><span className="empty-symbol">∅</span><strong>{title}</strong><p>{text}</p>{action}</div>;
}

function normalizeLogs(entries) {
  if (Array.isArray(entries)) return entries;
  if (typeof entries === 'string') return entries.split('\n').filter(Boolean);
  return [];
}

function LogEntries({ entries }) {
  const normalized = normalizeLogs(entries).map((entry) => typeof entry === 'string' ? { message: entry } : entry).filter(Boolean);
  return <div className="log-list">{normalized.length ? normalized.map((entry, index) => <div className="log-entry" key={entry.id || `${entry.created_at || entry.timestamp || ''}-${index}`}><span>{formatDate(entry.created_at || entry.timestamp)}</span><code>{entry.message || entry.text || JSON.stringify(entry)}</code></div>) : <span className="subtle-note">Keine Meldungen vorhanden.</span>}</div>;
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function duration(start, end) {
  const milliseconds = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '—';
  const seconds = Math.round(milliseconds / 1000);
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
