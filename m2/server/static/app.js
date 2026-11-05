import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";

const h = React.createElement;
const KIND_NAMES = { source: "Quelle", process: "Verarbeitung", action: "Aktion", control: "Steuerung" };
const STATE_NAMES = { queued: "Warteschlange", running: "In Arbeit", done: "Fertig", failed: "Fehlgeschlagen", active: "Aktiv", finished: "Abgeschlossen" };
const EVENT_NAMES = {
  "clip.created": "Clip angelegt",
  "clip.entered_node": "Knoten erreicht",
  "task.queued": "Aufgabe eingereiht",
  "task.leased": "Aufgabe übernommen",
  "task.progress": "Fortschritt aktualisiert",
  "task.succeeded": "Aufgabe erfolgreich",
  "task.failed": "Aufgabe fehlgeschlagen",
  "task.requeued": "Aufgabe erneut eingereiht",
  "clip.finished": "Clip abgeschlossen",
  "clip.failed": "Clip fehlgeschlagen",
  "worker.registered": "Worker registriert",
  "worker.heartbeat": "Worker meldet sich",
};

function api(path, options = {}) {
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers };
  return fetch(`/api${path}`, { ...options, headers }).then(async (response) => {
    if (response.status === 204) return null;
    const text = await response.text();
    if (!response.ok) {
      let message = text || `HTTP ${response.status}`;
      try {
        const body = JSON.parse(text);
        message = body.detail || message;
      } catch (_) {
        // Keep the response text for non-JSON errors.
      }
      throw new Error(message);
    }
    return text ? JSON.parse(text) : null;
  });
}

function makeInitialNodes(graph, manifests) {
  const byName = new Map(manifests.map((manifest) => [manifest.name, manifest]));
  return (graph?.nodes || []).map((node) => ({
    id: node.id,
    type: "workflowNode",
    position: node.position || { x: 80, y: 80 },
    data: {
      moduleName: node.module,
      manifest: byName.get(node.module),
      params: node.params || {},
      retry: node.retry || { max_attempts: 3 },
    },
  }));
}

function makeInitialEdges(graph) {
  return (graph?.edges || []).map((edge) => ({
    id: edge.id,
    source: edge.source,
    sourceHandle: edge.source_port,
    target: edge.target,
    targetHandle: "in",
    type: "smoothstep",
    label: edge.source_port,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: edge.source_port === "error" ? { stroke: "#c95043" } : undefined,
  }));
}

function WorkflowNode({ id, data, selected }) {
  const manifest = data.manifest;
  const inputs = manifest?.inputs || [];
  const outputs = manifest?.outputs || [];
  const hasSource = manifest?.kind === "source" || inputs.length === 0;
  const rowCount = Math.max(inputs.length, outputs.length, 1);
  const handleTop = (index) => `${Math.round(((index + 1) / (rowCount + 1)) * 100)}%`;
  const counts = data.counts || {};
  const badges = [
    ["queued", "Warteschlange"],
    ["running", "In Arbeit"],
    ["done", "Fertig"],
    ["failed", "Fehlgeschlagen"],
  ];
  return h("div", { className: `workflow-node${selected ? " selected" : ""}` },
    !hasSource && h(Handle, { type: "target", position: Position.Left, id: "in", style: { top: "50%" } }),
    outputs.map((port, index) => h(Handle, {
      key: port,
      type: "source",
      position: Position.Right,
      id: port,
      style: { top: handleTop(index) },
    })),
    h("div", { className: "node-top" },
      h("div", { className: "node-name" }, data.moduleName || id),
      h("span", { className: "node-kind" }, KIND_NAMES[manifest?.kind] || manifest?.kind || "Modul")),
    data.monitoring && h("div", { className: "node-statuses" }, badges.map(([key, label]) =>
      Number(counts[key] || 0) > 0 && h("span", { className: `count-chip ${key}`, key, title: label }, `${label}: ${counts[key]}`))),
    inputs.length > 0 && h("div", { className: "port-list", style: { padding: "0 12px 10px" } },
      inputs.map((port) => h("span", { className: "port-chip", key: port }, `Eingang · ${port}`))),
    outputs.length > 0 && h("div", { className: "port-list", style: { padding: "0 12px 10px" } },
      outputs.map((port) => h("span", { className: `port-chip${port === "error" ? " error" : ""}`, key: port }, `Ausgang · ${port}`))));
}
const nodeTypes = { workflowNode: WorkflowNode };

function FormField({ label, required, children, hint }) {
  return h("div", { className: "form-field" },
    h("label", null, label, required ? " · erforderlich" : ""),
    children,
    hint && h("span", { className: "form-hint" }, hint));
}

function SchemaField({ name, schema, value, required, onChange }) {
  const type = schema.type || "string";
  const label = schema.title || name;
  const [jsonError, setJsonError] = useState("");
  const [jsonDraft, setJsonDraft] = useState("");
  const [inputDraft, setInputDraft] = useState("");
  useEffect(() => {
    setJsonDraft(value === undefined ? "" : JSON.stringify(value, null, 2));
    setInputDraft(String(value ?? schema.default ?? ""));
  }, [value, schema.default]);
  if (type === "boolean") {
    return h("div", { className: "form-field" },
      h("label", null,
        h("input", { type: "checkbox", checked: Boolean(value ?? schema.default ?? false), onChange: (event) => onChange(event.target.checked) }),
        ` ${label}${required ? " · erforderlich" : ""}`),
      schema.description && h("span", { className: "form-hint" }, schema.description));
  }
  if (type === "array" || type === "object") {
    return h(FormField, { label, required, hint: schema.description || (type === "array" ? "JSON-Liste" : "JSON-Objekt") },
      h("textarea", {
        value: jsonDraft,
        spellCheck: false,
        placeholder: type === "array" ? "[]" : "{}",
        onChange: (event) => {
          setJsonError("");
          const raw = event.target.value;
          setJsonDraft(raw);
          if (!raw.trim()) return onChange(undefined);
          try {
            const parsed = JSON.parse(raw);
            if ((type === "array" && !Array.isArray(parsed)) || (type === "object" && (parsed === null || Array.isArray(parsed) || typeof parsed !== "object"))) {
              setJsonError(type === "array" ? "Bitte eine JSON-Liste eingeben." : "Bitte ein JSON-Objekt eingeben.");
              return;
            }
            onChange(parsed);
          } catch (_) {
            setJsonError("JSON ist noch nicht gültig.");
          }
        },
      }),
      jsonError && h("span", { className: "form-hint", style: { color: "#b43d39" } }, jsonError));
  }
  if (Array.isArray(schema.enum)) {
    const selected = value === undefined ? "" : String(value);
    return h(FormField, { label, required, hint: schema.description },
      h("select", {
        value: selected,
        onChange: (event) => {
          const option = schema.enum.find((item) => String(item) === event.target.value);
          onChange(option);
        },
      }, h("option", { value: "" }, "Auswählen …"), schema.enum.map((item) => h("option", { key: String(item), value: String(item) }, String(item)))));
  }
  const inputType = type === "integer" || type === "number" ? "number" : "text";
  return h(FormField, { label, required, hint: schema.description },
    h("input", {
      type: inputType,
      value: inputDraft,
      min: schema.minimum,
      max: schema.maximum,
      step: type === "integer" ? 1 : type === "number" ? "any" : undefined,
      onChange: (event) => {
        const raw = event.target.value;
        setInputDraft(raw);
        if (!raw) return onChange(undefined);
        if (inputType !== "number") return onChange(raw);
        const parsed = Number(raw);
        if (Number.isFinite(parsed) && (type !== "integer" || Number.isInteger(parsed))) onChange(parsed);
      },
    }));
}

function WorkflowEditor({ workflow, modules, onSaved, onError, onNotice, onInject }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setNodes(makeInitialNodes(workflow?.graph, modules));
    setEdges(makeInitialEdges(workflow?.graph));
    setSelectedId(null);
  }, [workflow?.id, workflow?.latest_version, workflow?.graph, modules, setEdges, setNodes]);
  const selectedNode = nodes.find((node) => node.id === selectedId);

  const addModule = (manifest) => {
    const nodeId = `n-${Date.now()}`;
    const index = nodes.length;
    const defaults = {};
    for (const [key, property] of Object.entries(manifest.params_schema?.properties || {})) {
      if (Object.prototype.hasOwnProperty.call(property, "default")) defaults[key] = property.default;
    }
    setNodes((current) => [...current, {
      id: nodeId,
      type: "workflowNode",
      position: { x: 70 + (index % 3) * 280, y: 65 + Math.floor(index / 3) * 190 },
      data: { moduleName: manifest.name, manifest, params: defaults, retry: { max_attempts: 3 } },
    }]);
    setSelectedId(nodeId);
  };

  const connect = (connection) => {
    if (!connection.sourceHandle || !connection.targetHandle) return;
    const port = connection.sourceHandle;
    setEdges((current) => addEdge({
      ...connection,
      id: `e-${Date.now()}`,
      type: "smoothstep",
      label: port,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: port === "error" ? { stroke: "#c95043" } : undefined,
    }, current));
  };

  const updateNodeData = (id, key, value) => setNodes((current) => current.map((node) =>
    node.id === id ? { ...node, data: { ...node.data, [key]: value } } : node));
  const updateParam = (id, key, value) => {
    const node = nodes.find((item) => item.id === id);
    if (!node) return;
    const params = { ...(node.data.params || {}) };
    if (value === undefined) delete params[key];
    else params[key] = value;
    updateNodeData(id, "params", params);
  };
  const removeSelected = () => {
    if (!selectedNode) return;
    setNodes((current) => current.filter((node) => node.id !== selectedNode.id));
    setEdges((current) => current.filter((edge) => edge.source !== selectedNode.id && edge.target !== selectedNode.id));
    setSelectedId(null);
  };

  const saveVersion = async () => {
    setSaving(true);
    try {
      const graph = {
        nodes: nodes.map((node) => ({
          id: node.id,
          module: node.data.moduleName,
          params: node.data.params || {},
          position: { x: node.position.x, y: node.position.y },
          retry: node.data.retry || { max_attempts: 3 },
        })),
        edges: edges.map((edge) => ({
          id: edge.id,
          source: edge.source,
          source_port: edge.sourceHandle || edge.label,
          target: edge.target,
        })),
      };
      const result = await api(`/workflows/${encodeURIComponent(workflow.id)}/versions`, {
        method: "POST",
        body: JSON.stringify({ graph }),
      });
      onNotice(`Neue Workflow-Version ${result?.version ?? ""} gespeichert.`);
      await onSaved();
    } catch (error) {
      onError(error.message);
    } finally {
      setSaving(false);
    }
  };

  return h(React.Fragment, null,
    h("div", { className: "page-heading" },
      h("div", null,
        h("h1", null, workflow.name),
        h("p", null, `Workflow · Version ${workflow.latest_version ?? "—"} · Änderungen werden als neue Version gespeichert`)),
      h("div", { className: "heading-actions" },
        h("button", { className: "button", onClick: onInject }, "＋ Clip einspeisen"),
        h("button", { className: "button primary", onClick: saveVersion, disabled: saving }, saving ? "Speichert …" : "Neue Version speichern"))),
    h("div", { className: "editor-layout" },
      h("section", { className: "panel editor-main", "aria-label": "Workflow-Editor" },
        h("div", { className: "editor-toolbar" },
          h("strong", null, "Workflow-Graph"),
          h("span", null, `${nodes.length} Knoten · ${edges.length} Verbindungen`)),
        h("div", { className: "editor-canvas" },
          h(ReactFlow, {
            nodes,
            edges,
            nodeTypes,
            onNodesChange,
            onEdgesChange,
            onConnect: connect,
            onNodeClick: (_, node) => setSelectedId(node.id),
            onPaneClick: () => setSelectedId(null),
            fitView: true,
            fitViewOptions: { padding: 0.2 },
            deleteKeyCode: ["Backspace", "Delete"],
            defaultEdgeOptions: { type: "smoothstep", markerEnd: { type: MarkerType.ArrowClosed } },
          },
            h(Background, { color: "#dfe7ee", gap: 19, size: 1 }),
            h(MiniMap, { pannable: true, zoomable: true }),
            h(Controls, null)))),
      h("aside", { className: "editor-side" },
        h("section", { className: "panel side-panel" },
          h("h3", null, "Modul-Palette"),
          modules.length === 0
            ? h("div", { className: "inline-empty" }, "Keine Module verfügbar.")
            : h("div", { className: "palette-list" }, modules.map((manifest) => h("button", {
              className: "palette-item",
              key: manifest.name,
              onClick: () => addModule(manifest),
              title: `Modul ${manifest.name} hinzufügen`,
            }, h("span", { className: "module-label" }, manifest.name), h("span", { className: "kind-label" }, KIND_NAMES[manifest.kind] || manifest.kind))))),
        h("section", { className: "panel side-panel" },
          h("h3", null, selectedNode ? "Knoten konfigurieren" : "Knoten-Details"),
          !selectedNode
            ? h("p", null, "Wähle einen Knoten aus oder füge ein Modul aus der Palette hinzu.")
            : h("div", { className: "inspector-scroll" },
              h("div", { className: "muted", style: { marginBottom: "12px" } }, `${selectedNode.data.moduleName} · ${selectedNode.id}`),
              Object.entries(selectedNode.data.manifest?.params_schema?.properties || {}).length === 0
                ? h("p", null, "Dieses Modul hat keine konfigurierbaren Parameter.")
                : Object.entries(selectedNode.data.manifest.params_schema.properties).map(([key, schema]) => h(SchemaField, {
                  key,
                  name: key,
                  schema,
                  value: selectedNode.data.params?.[key],
                  required: (selectedNode.data.manifest.params_schema.required || []).includes(key),
                  onChange: (value) => updateParam(selectedNode.id, key, value),
                })),
              h(FormField, { label: "Maximale Versuche", hint: "Wiederholungsregel dieses Knotens" },
                h("input", {
                  type: "number",
                  min: 1,
                  step: 1,
                  value: selectedNode.data.retry?.max_attempts ?? 3,
                  onChange: (event) => updateNodeData(selectedNode.id, "retry", {
                    ...selectedNode.data.retry,
                    max_attempts: Number(event.target.value),
                  }),
                })),
              h("div", { className: "field-label" }, "Ausgänge"),
              h("div", { className: "port-list" }, (selectedNode.data.manifest?.outputs || []).map((port) => h("span", {
                className: `port-chip${port === "error" ? " error" : ""}`,
                key: port,
              }, port))),
              h("button", { className: "button small danger", style: { marginTop: "14px" }, onClick: removeSelected }, "Knoten entfernen"))))));
}

function StatePill({ state }) {
  return h("span", { className: `state-pill ${state || ""}` }, STATE_NAMES[state] || state || "Unbekannt");
}

function MetricCard({ label, value }) {
  return h("section", { className: "panel metric-card" }, h("div", { className: "metric-label" }, label), h("div", { className: "metric-value" }, value));
}

function WorkflowMonitor({ workflow, modules, summary, workers }) {
  const byName = new Map(modules.map((manifest) => [manifest.name, manifest]));
  const graphNodes = (workflow.graph?.nodes || []).map((node) => ({
    id: node.id,
    type: "workflowNode",
    position: node.position || { x: 80, y: 80 },
    data: {
      moduleName: node.module,
      manifest: byName.get(node.module),
      monitoring: true,
      counts: summary?.nodes?.[node.id] || {},
    },
  }));
  const graphEdges = makeInitialEdges(workflow.graph);
  const clipCounts = summary?.clips || {};
  const workerList = Array.isArray(workers) ? workers : [];
  return h(React.Fragment, null,
    h("div", { className: "metrics-row" },
      h(MetricCard, { label: "Aktive Clips", value: clipCounts.active ?? "—" }),
      h(MetricCard, { label: "Abgeschlossen", value: clipCounts.finished ?? "—" }),
      h(MetricCard, { label: "Fehlgeschlagen", value: clipCounts.failed ?? "—" })),
    h("div", { className: "monitor-grid" },
      h("section", { className: "panel monitor-graph", "aria-label": "Live-Workflow" },
        h(ReactFlow, { nodes: graphNodes, edges: graphEdges, nodeTypes, fitView: true, nodesDraggable: false, nodesConnectable: false, elementsSelectable: false, proOptions: { hideAttribution: true } },
          h(Background, { color: "#dfe7ee", gap: 19, size: 1 }), h(MiniMap, null), h(Controls, null))),
      h("section", { className: "panel worker-panel" },
        h("h2", null, `Worker · ${workerList.length}`),
        workerList.length === 0 ? h("div", { className: "inline-empty" }, "Keine Worker gemeldet.")
          : h("div", { className: "worker-list" }, workerList.map((worker) => h("article", { className: "worker-card", key: worker.id },
            h("div", { className: "worker-card-head" }, h("span", null, worker.name || worker.id), h("span", { className: "worker-status" }, worker.status || "—")),
            h("div", { className: "worker-meta" },
              h("div", null, `Fähigkeiten: ${(worker.capabilities || []).join(", ") || "—"}`),
              h("div", null, `Tags: ${(worker.tags || []).join(", ") || "—"}`),
              h("div", null, `Letzter Heartbeat: ${formatDate(worker.last_heartbeat)}`))))))));
}

function TaskMonitor({ nodes, selectedNodeId, setSelectedNodeId, tasks, taskLogs, retryTask, retrying }) {
  const activeNode = nodes.find((node) => node.id === selectedNodeId);
  const list = Array.isArray(tasks) ? tasks : [];
  const nodeButtons = nodes.map((node) => h("button", {
    className: `select-row${node.id === selectedNodeId ? " active" : ""}`,
    key: node.id,
    onClick: () => setSelectedNodeId(node.id),
  }, h("strong", null, node.module), h("small", null, node.id)));
  const taskCards = list.map((task, index) => {
    const taskId = task.id ?? task.task_id;
    const state = task.state;
    const lines = taskLogs[taskId] || [];
    const logText = Array.isArray(lines) ? lines.join("\n") : String(lines || "");
    return h("article", { className: "panel task-card", key: taskId || index },
      h("div", { className: "task-head" },
        h("div", { className: "task-title" }, `Aufgabe ${taskId || "—"}`, h(StatePill, { state })),
        state === "failed" && taskId && h("button", {
          className: "button small",
          onClick: () => retryTask(taskId),
          disabled: retrying === taskId,
        }, retrying === taskId ? "Wird erneut freigegeben …" : "Erneut freigeben")),
      h("div", { className: "task-body" },
        h("div", { className: "task-meta" },
          h("span", null, `Versuch: ${task.attempt ?? "—"}`),
          h("span", null, `Worker: ${task.worker_id || "—"}`),
        ),
        h("div", { className: "field-label", style: { marginTop: "12px" } }, "Protokolle"),
        h("pre", { className: "log-box" }, logText || "Noch keine Log-Einträge empfangen.")));
  });
  return h("div", { className: "two-column" },
    h("section", { className: "panel list-panel" },
      h("h2", null, "Workflow-Knoten"),
      nodes.length === 0
        ? h("div", { className: "inline-empty" }, "Der Workflow enthält keine Knoten.")
        : h("div", { className: "select-list" }, nodeButtons)),
    h("section", { className: "panel list-panel" },
      h("h2", null, activeNode ? `Aufgaben · ${activeNode.module}` : "Aufgaben"),
      !activeNode
        ? h("div", { className: "inline-empty" }, "Wähle einen Knoten aus.")
        : list.length === 0
          ? h("div", { className: "inline-empty" }, "Für diesen Knoten gibt es keine Aufgaben.")
          : taskCards));
}

function ClipMonitor({ clips, selectedClipId, setSelectedClipId, clipDetail }) {
  const clipList = Array.isArray(clips) ? clips : [];
  const events = Array.isArray(clipDetail?.events) ? clipDetail.events : [];
  const selectedClip = clipDetail?.clip || clipList.find((clip) => clip.id === selectedClipId);
  const clipButtons = clipList.map((clip) => h("button", {
    className: `select-row${clip.id === selectedClipId ? " active" : ""}`,
    key: clip.id,
    onClick: () => setSelectedClipId(clip.id),
  }, h("strong", null, clip.name || clip.id), h("small", null,
    `${clip.job || ""}${clip.state ? ` · ${STATE_NAMES[clip.state] || clip.state}` : ""}`)));
  const timeline = events.map((event, index) => h("article", { className: "timeline-item", key: event.id || index },
    h("span", { className: "timeline-dot" }),
    h("div", { className: "timeline-date" }, formatDate(event.ts)),
    h("div", { className: "timeline-title" }, EVENT_NAMES[event.type] || event.type || "Ereignis"),
    h("div", { className: "timeline-detail" },
      [event.node_id && `Knoten: ${event.node_id}`, event.task_id && `Aufgabe: ${event.task_id}`, event.worker_id && `Worker: ${event.worker_id}`].filter(Boolean).join(" · "),
      event.data !== undefined && h("pre", { className: "log-box" }, JSON.stringify(event.data, null, 2)))));
  return h("div", { className: "two-column" },
    h("section", { className: "panel list-panel" },
      h("h2", null, "Clips im Workflow"),
      clipList.length === 0 ? h("div", { className: "inline-empty" }, "Noch keine Clips vorhanden.")
        : h("div", { className: "select-list" }, clipButtons)),
    h("section", { className: "panel list-panel" },
      h("h2", null, selectedClip ? `Zeitstrahl · ${selectedClip.name || selectedClip.id}` : "Clip-Zeitstrahl"),
      !selectedClipId
        ? h("div", { className: "inline-empty" }, "Wähle einen Clip aus.")
        : events.length === 0
          ? h("div", { className: "inline-empty" }, "Für diesen Clip liegen noch keine Events vor.")
          : h("div", { className: "timeline" }, timeline)));
}

function WorkflowModal({ onClose, onCreate, busy }) {
  const [name, setName] = useState("");
  return h("div", { className: "modal-backdrop", onMouseDown: (event) => event.target === event.currentTarget && onClose() },
    h("form", { className: "modal", onSubmit: (event) => { event.preventDefault(); onCreate(name); } },
      h("h2", null, "Workflow anlegen"),
      h(FormField, { label: "Name", required: true }, h("input", { autoFocus: true, required: true, value: name, onChange: (event) => setName(event.target.value), maxLength: 200 })),
      h("div", { className: "modal-actions" },
        h("button", { type: "button", className: "button", onClick: onClose }, "Abbrechen"),
        h("button", { type: "submit", className: "button primary", disabled: busy || !name.trim() }, busy ? "Wird angelegt …" : "Workflow anlegen"))));
}

function ClipModal({ onClose, onCreate, busy }) {
  const [name, setName] = useState("");
  const [job, setJob] = useState("");
  const [metadata, setMetadata] = useState("{}");
  const [files, setFiles] = useState("[]");
  const [formError, setFormError] = useState("");
  const submit = (event) => {
    event.preventDefault();
    try {
      const parsedMetadata = JSON.parse(metadata);
      const parsedFiles = JSON.parse(files);
      if (!parsedMetadata || Array.isArray(parsedMetadata) || typeof parsedMetadata !== "object") throw new Error("Metadaten müssen ein JSON-Objekt sein.");
      if (!Array.isArray(parsedFiles)) throw new Error("Dateien müssen eine JSON-Liste sein.");
      setFormError("");
      onCreate({ name, job, metadata: parsedMetadata, files: parsedFiles });
    } catch (error) {
      setFormError(error.message || "JSON ist ungültig.");
    }
  };
  return h("div", { className: "modal-backdrop", onMouseDown: (event) => event.target === event.currentTarget && onClose() },
    h("form", { className: "modal", onSubmit: submit },
      h("h2", null, "Clip einspeisen"),
      h(FormField, { label: "Name", required: true }, h("input", { autoFocus: true, required: true, value: name, onChange: (event) => setName(event.target.value) })),
      h(FormField, { label: "Job", required: true }, h("input", { required: true, value: job, onChange: (event) => setJob(event.target.value) })),
      h(FormField, { label: "Metadaten", hint: "JSON-Objekt" }, h("textarea", { value: metadata, onChange: (event) => setMetadata(event.target.value), spellCheck: false })),
      h(FormField, { label: "Dateien", hint: "JSON-Liste mit Datei-Referenzen" }, h("textarea", { value: files, onChange: (event) => setFiles(event.target.value), spellCheck: false })),
      formError && h("div", { className: "error-banner" }, formError),
      h("div", { className: "modal-actions" },
        h("button", { type: "button", className: "button", onClick: onClose }, "Abbrechen"),
        h("button", { type: "submit", className: "button primary", disabled: busy || !name.trim() || !job.trim() }, busy ? "Wird eingespeist …" : "Clip einspeisen"))));
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "medium" }).format(date);
}

function App() {
  const [health, setHealth] = useState(null);
  const [modules, setModules] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [workflow, setWorkflow] = useState(null);
  const [page, setPage] = useState("workflows");
  const [monitorTab, setMonitorTab] = useState("workflow");
  const [summary, setSummary] = useState(null);
  const [workers, setWorkers] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [clips, setClips] = useState([]);
  const [clipDetail, setClipDetail] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [selectedClipId, setSelectedClipId] = useState("");
  const [taskLogs, setTaskLogs] = useState({});
  const [liveStatus, setLiveStatus] = useState("Verbinde …");
  const [liveTick, setLiveTick] = useState(0);
  const [refreshToken, setRefreshToken] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modal, setModal] = useState("");
  const [busy, setBusy] = useState(false);
  const [retrying, setRetrying] = useState("");

  const loadWorkflows = useCallback(async () => {
    const result = await api("/workflows");
    setWorkflows(Array.isArray(result) ? result : []);
  }, []);
  const loadWorkflow = useCallback(async (id) => {
    if (!id) {
      setWorkflow(null);
      return;
    }
    const result = await api(`/workflows/${encodeURIComponent(id)}`);
    setWorkflow(result);
    const firstNode = result.graph?.nodes?.[0];
    setSelectedNodeId((current) => current || firstNode?.id || "");
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([api("/health"), api("/modules"), api("/workflows")]).then(([serverHealth, manifests, items]) => {
      if (!active) return;
      setHealth(serverHealth);
      setModules(Array.isArray(manifests) ? manifests : []);
      setWorkflows(Array.isArray(items) ? items : []);
      if (items?.length) setSelectedId(items[0].id);
    }).catch((loadError) => {
      if (active) setError(`Daten konnten nicht geladen werden: ${loadError.message}`);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setWorkflow(null);
      return;
    }
    let active = true;
    setSummary(null);
    setWorkers([]);
    setTasks([]);
    setClips([]);
    setClipDetail(null);
    setSelectedClipId("");
    api(`/workflows/${encodeURIComponent(selectedId)}`).then((result) => {
      if (!active) return;
      setWorkflow(result);
      const firstNode = result.graph?.nodes?.[0];
      setSelectedNodeId(firstNode?.id || "");
    }).catch((loadError) => {
      if (active) setError(`Workflow konnte nicht geladen werden: ${loadError.message}`);
    });
    return () => { active = false; };
  }, [selectedId]);

  useEffect(() => {
    let stream;
    try {
      stream = new EventSource("/api/events/stream");
      stream.onopen = () => setLiveStatus("Live verbunden");
      stream.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data);
          if (event.type === "task.progress" && event.task_id && Array.isArray(event.data?.log)) {
            setTaskLogs((current) => ({
              ...current,
              [event.task_id]: [...(current[event.task_id] || []), ...event.data.log],
            }));
          }
          setLiveTick((tick) => tick + 1);
        } catch (_) {
          setLiveStatus("Live-Daten nicht lesbar");
        }
      };
      stream.onerror = () => setLiveStatus("Live-Verbindung wird wiederhergestellt");
    } catch (_) {
      setLiveStatus("Live-Verbindung nicht verfügbar");
    }
    return () => stream?.close();
  }, []);

  useEffect(() => {
    if (!selectedId || page !== "monitor") return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      Promise.all([
        api(`/workflows/${encodeURIComponent(selectedId)}/summary`),
        api("/workers"),
        api(`/clips?workflow_id=${encodeURIComponent(selectedId)}`),
      ]).then(([nextSummary, nextWorkers, nextClips]) => {
        if (!active) return;
        setSummary(nextSummary);
        setWorkers(Array.isArray(nextWorkers) ? nextWorkers : []);
        const nextClipList = Array.isArray(nextClips) ? nextClips : [];
        setClips(nextClipList);
        setSelectedClipId((current) => current || nextClipList[0]?.id || "");
      }).catch((loadError) => {
        if (active) setError(`Live-Status konnte nicht geladen werden: ${loadError.message}`);
      });
    }, 80);
    return () => { active = false; window.clearTimeout(timer); };
  }, [selectedId, page, liveTick, refreshToken]);

  useEffect(() => {
    if (page !== "monitor" || monitorTab !== "node" || !selectedId || !selectedNodeId) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      api(`/workflows/${encodeURIComponent(selectedId)}/nodes/${encodeURIComponent(selectedNodeId)}/tasks`)
        .then((result) => { if (active) setTasks(Array.isArray(result) ? result : []); })
        .catch((loadError) => { if (active) setError(`Aufgaben konnten nicht geladen werden: ${loadError.message}`); });
    }, 80);
    return () => { active = false; window.clearTimeout(timer); };
  }, [selectedId, selectedNodeId, page, monitorTab, liveTick, refreshToken]);

  useEffect(() => {
    if (page !== "monitor" || monitorTab !== "clip" || !selectedClipId) {
      if (monitorTab !== "clip") setClipDetail(null);
      return undefined;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      api(`/clips/${encodeURIComponent(selectedClipId)}`)
        .then((result) => { if (active) setClipDetail(result); })
        .catch((loadError) => { if (active) setError(`Clip-Zeitstrahl konnte nicht geladen werden: ${loadError.message}`); });
    }, 80);
    return () => { active = false; window.clearTimeout(timer); };
  }, [selectedClipId, page, monitorTab, liveTick, refreshToken]);

  const createWorkflow = async (name) => {
    setBusy(true);
    setError("");
    try {
      const created = await api("/workflows", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      const nextList = await api("/workflows");
      setWorkflows(Array.isArray(nextList) ? nextList : []);
      setSelectedId(created.id);
      setPage("workflows");
      setWorkflow({ ...created, latest_version: created.latest_version, graph: { nodes: [], edges: [] } });
      setModal("");
      setNotice("Workflow angelegt. Füge Module hinzu und speichere anschließend eine neue Version.");
    } catch (createError) {
      setError(`Workflow konnte nicht angelegt werden: ${createError.message}`);
    } finally {
      setBusy(false);
    }
  };
  const injectClip = async (clip) => {
    if (!selectedId) return;
    setBusy(true);
    setError("");
    try {
      await api(`/workflows/${encodeURIComponent(selectedId)}/clips`, { method: "POST", body: JSON.stringify(clip) });
      setModal("");
      setNotice("Clip wurde eingespeist.");
      setRefreshToken((token) => token + 1);
    } catch (injectError) {
      setError(`Clip konnte nicht eingespeist werden: ${injectError.message}`);
    } finally {
      setBusy(false);
    }
  };
  const retryTask = async (taskId) => {
    setRetrying(taskId);
    setError("");
    try {
      await api(`/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
      setNotice("Aufgabe wurde erneut freigegeben.");
      setRefreshToken((token) => token + 1);
    } catch (retryError) {
      setError(`Aufgabe konnte nicht erneut freigegeben werden: ${retryError.message}`);
    } finally {
      setRetrying("");
    }
  };

  const selectedWorkflow = workflows.find((item) => item.id === selectedId);
  const workflowTitle = workflow?.name || selectedWorkflow?.name || "Workflow";
  const showNotice = notice && h("div", { className: "notice-banner" }, notice);
  const content = page === "workflows"
    ? workflow
      ? h(WorkflowEditor, {
        key: `${workflow.id}-${workflow.latest_version}`,
        workflow,
        modules,
        onError: setError,
        onNotice: (message) => { setNotice(message); window.setTimeout(() => setNotice(""), 5000); },
        onSaved: async () => {
          await loadWorkflows();
          await loadWorkflow(selectedId);
        },
        onInject: () => setModal("clip"),
      })
      : h("div", { className: "panel empty-state" }, h("div", null,
        h("div", { className: "empty-icon" }, "◇"),
        h("strong", null, "Noch kein Workflow ausgewählt"),
        h("span", null, "Lege einen Workflow an oder wähle einen aus der Liste.")))
    : !workflow
      ? h("div", { className: "panel empty-state" }, h("div", null,
        h("div", { className: "empty-icon" }, "◷"),
        h("strong", null, "Kein Workflow ausgewählt"),
        h("span", null, "Wähle zuerst einen Workflow aus der Liste.")))
      : h(React.Fragment, null,
        h("div", { className: "page-heading" },
          h("div", null, h("h1", null, workflowTitle), h("p", null, "Live-Überwachung aus Workflow-Status, Aufgaben und Clip-Events")),
          h("div", { className: "heading-actions" }, h("span", { className: "state-pill running" }, liveStatus))),
        h("div", { className: "monitor-tabs" },
          [["workflow", "Workflow"], ["node", "Knoten"], ["clip", "Clip"]].map(([key, label]) => h("button", {
            className: `monitor-tab${monitorTab === key ? " active" : ""}`,
            key,
            onClick: () => setMonitorTab(key),
          }, label))),
        monitorTab === "workflow"
          ? h(WorkflowMonitor, { workflow, modules, summary, workers })
          : monitorTab === "node"
            ? h(TaskMonitor, {
              nodes: workflow.graph?.nodes || [],
              selectedNodeId,
              setSelectedNodeId,
              tasks,
              taskLogs,
              retryTask,
              retrying,
            })
            : h(ClipMonitor, { clips, selectedClipId, setSelectedClipId, clipDetail }));

  return h(React.Fragment, null,
    h("header", { className: "app-header" },
      h("div", { className: "brand" },
        h("div", { className: "brand-mark" }, "m2"),
        h("div", null, h("div", { className: "brand-title" }, "Workflow-Editor"), h("div", { className: "brand-subtitle" }, "Prototyp · Orchestrierung"))),
      h("div", { className: "header-meta" },
        h("span", { className: `pulse${liveStatus === "Live verbunden" ? "" : " offline"}` }),
        h("span", null, liveStatus),
        h("span", null, `Server ${health?.version ? `v${health.version}` : "—"}`))),
    h("nav", { className: "top-nav", "aria-label": "Hauptnavigation" },
      h("button", { className: `nav-button${page === "workflows" ? " active" : ""}`, onClick: () => setPage("workflows") }, "Workflows"),
      h("button", { className: `nav-button${page === "monitor" ? " active" : ""}`, onClick: () => setPage("monitor") }, "Live-Überwachung")),
    h("div", { className: "app-layout" },
      h("aside", { className: "sidebar" },
        h("div", { className: "side-heading" }, h("h2", null, "Workflows"), h("button", { className: "icon-button", title: "Workflow anlegen", onClick: () => setModal("workflow") }, "+")),
        workflows.length === 0 ? h("div", { className: "inline-empty" }, "Noch keine Workflows.")
          : h("div", { className: "workflow-items" }, workflows.map((item) => h("button", {
            className: `workflow-item${item.id === selectedId ? " active" : ""}`,
            key: item.id,
            onClick: () => { setSelectedId(item.id); setError(""); },
          }, h("span", { className: "workflow-name" }, item.name), h("span", { className: "workflow-version" }, `Version ${item.latest_version ?? "—"}`))))),
      h("main", { className: "content" },
        error && h("div", { className: "error-banner", role: "alert" }, error, h("button", { className: "icon-button", style: { float: "right", marginTop: "-5px" }, onClick: () => setError("") }, "×")),
        showNotice,
        content)),
    modal === "workflow" && h(WorkflowModal, { onClose: () => setModal(""), onCreate: createWorkflow, busy }),
    modal === "clip" && h(ClipModal, { onClose: () => setModal(""), onCreate: injectClip, busy }));
}

createRoot(document.getElementById("root")).render(h(App));
