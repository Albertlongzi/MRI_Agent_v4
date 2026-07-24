/* ═══════════════════════════════════════════════════════
   MRI AI STUDIO — Frontend
   Set window.API_BASE_URL before this script if your
   backend is on a different host/port, e.g.:
       window.API_BASE_URL = "http://127.0.0.1:18008";
═══════════════════════════════════════════════════════ */
const API_BASE = (typeof window !== "undefined" && window.API_BASE_URL)
  ? window.API_BASE_URL.replace(/\/$/, "")
  : "";

// Patch fetch helpers to prepend API_BASE
/* ═══════════════════════════════════════════════════
   MRI_Agent v4  —  Clinical Workstation Frontend
   Siemens Healthineers-inspired design
═══════════════════════════════════════════════════ */

// ── Fallback demo state ──────────────────────────
const fallbackSnapshot = {
  session: {
    session_id: "session-demo",
    case_state: {
      case_id: "prostate_demo_001",
      domain: "prostate",
      input_root: "/demo/cases/sub-019_2",
      sequence_index: {},
      available_modalities: ["T2w", "ADC", "DWI_highb"],
      active_graph_id: "graph-prostate-demo",
      active_node_id: "identify_sequences",
      selected_artifacts: [],
      last_error: "register_adc failed: SimpleITK image size mismatch",
      last_event_id: "event-3",
      ui_focus: { panel: "graph", selected_node: "identify_sequences" },
    },
    graph: null,
    chat_history: [
      { role: "user",      content: "Inspect this prostate case, register ADC to T2, segment the gland, and give me a short report." },
      { role: "assistant", content: "I've proposed a 6-node ActionGraph for this case.\n\nPipeline: Case Intake → Identify Sequences → Register ADC → Segment Prostate → Package VLM Evidence → Generate Report.\n\nClick Execute Next to advance, or ask me to modify the plan." },
    ],
  },
  graph: {
    graph_id: "graph-prostate-demo",
    case_id: "prostate_demo_001",
    domain: "prostate",
    status: "ready",
    version: 1,
    root_goal: "Inspect the prostate MRI, register ADC to T2w, segment the gland, and draft a short report.",
    nodes: [
      { node_id: "intake_case",          kind: "planner",  title: "Case Intake",           action_type: "read_case",             tool_name: null,                   status: "succeeded", depends_on: [],                       inputs: {},                                              outputs: { case_summary: "Prostate case with T2w, ADC, DWI_highb present." }, checks: ["case root exists", "modalities indexed"], artifact_refs: [], owner: "supervisor", editable: false, notes: "Case bootstrap node." },
      { node_id: "identify_sequences",   kind: "tool",     title: "Identify Sequences",    action_type: "identify_sequences",    tool_name: "identify_sequences",   status: "succeeded",   depends_on: ["intake_case"],           inputs: { dicom_case_dir: "@case.input" },               outputs: { mapping: {} },                              checks: ["sequence inventory complete"],              artifact_refs: [], owner: "executor",   editable: true,  notes: null },
      { node_id: "register_adc",         kind: "tool",     title: "Register ADC → T2w",    action_type: "register_to_reference", tool_name: "register_to_reference",status: "failed",   depends_on: ["identify_sequences"],    inputs: { fixed: "@seq.T2w", moving: "@seq.ADC" },      outputs: { resampled_path: "", transform_path: "" },   checks: ["fixed is T2w", "moving is non-T2w"],       artifact_refs: [], owner: "executor",   editable: true,  notes: null,
        last_error: "SimpleITK error: Fixed image size [512,512,24] does not match moving image size [256,256,20]. Ensure both volumes are in the same voxel space before registration.", error_ts: "2026-03-19T00:01:14Z", retry_count: 1,
        retry_history: [
          { attempt: 1, ts: "2026-03-19T00:01:02Z", status: "failed", error: "SimpleITK error: Fixed image size mismatch." }
        ],
        reflector_reasoning: "The ADC volume has a different voxel grid than the T2w. I will add a resampling pre-step to harmonize the voxel spacing to 0.5×0.5×1.0 mm before passing to register_to_reference. Retry #2 will use sitk.Resample with the T2w as the reference grid." },
      { node_id: "segment_prostate",     kind: "tool",     title: "Segment Prostate",      action_type: "segment_prostate",      tool_name: "segment_prostate",     status: "planned",   depends_on: ["register_adc"],          inputs: { t2w_ref: "@seq.T2w" },                        outputs: { prostate_mask_path: "" },                   checks: ["mask output present"],                     artifact_refs: [], owner: "planner",    editable: true,  notes: null },
      { node_id: "package_vlm_evidence", kind: "tool",     title: "Package Evidence",      action_type: "package_vlm_evidence",  tool_name: "package_vlm_evidence", status: "planned",   depends_on: ["segment_prostate"],      inputs: { case_state_path: "@runtime.case_state_path" },outputs: { vlm_evidence_path: "" },                   checks: ["evidence bundle written"],                 artifact_refs: [], owner: "executor",   editable: true,  notes: null },
      { node_id: "generate_report",      kind: "finalize", title: "Generate Report",       action_type: "generate_report",       tool_name: "generate_report",      status: "planned",   depends_on: ["package_vlm_evidence"],  inputs: { domain: "prostate" },                         outputs: { report_path: "" },                          checks: ["report references evidence"],              artifact_refs: [], owner: "planner",    editable: true,  notes: null },
    ],
    edges: [
      { edge_id: "e1", from_node: "intake_case",          to_node: "identify_sequences",  type: "control" },
      { edge_id: "e2", from_node: "identify_sequences",   to_node: "register_adc",         type: "control" },
      { edge_id: "e3", from_node: "register_adc",         to_node: "segment_prostate",     type: "control" },
      { edge_id: "e4", from_node: "segment_prostate",     to_node: "package_vlm_evidence", type: "control" },
      { edge_id: "e5", from_node: "package_vlm_evidence", to_node: "generate_report",      type: "control" },
    ],
    artifacts: [],
    events: [
      { event_id: "event-1", graph_id: "graph-prostate-demo", ts: "2026-03-19T00:00:00Z", actor_type: "human",      actor_id: "demo-user",  event_type: "graph_requested", target_id: "graph-prostate-demo", payload: { intent: "Inspect this prostate case and generate a short report." }, parent_event_id: null },
      { event_id: "event-2", graph_id: "graph-prostate-demo", ts: "2026-03-19T00:00:03Z", actor_type: "supervisor", actor_id: "supervisor", event_type: "graph_proposed",  target_id: "graph-prostate-demo", payload: { node_count: 6, status: "ready" },                                  parent_event_id: null },
      { event_id: "event-3", graph_id: "graph-prostate-demo", ts: "2026-03-19T00:00:06Z", actor_type: "system",     actor_id: "executor",   event_type: "graph_ready",           target_id: "graph-prostate-demo",  payload: { next_node: "identify_sequences" },                                                                   parent_event_id: null },
      { event_id: "event-4", graph_id: "graph-prostate-demo", ts: "2026-03-19T00:01:02Z", actor_type: "executor",   actor_id: "executor",   event_type: "node_failed",           target_id: "register_adc",         payload: { error: "SimpleITK: image size mismatch", attempt: 1 },                                                      parent_event_id: "event-3" },
      { event_id: "event-5", graph_id: "graph-prostate-demo", ts: "2026-03-19T00:01:08Z", actor_type: "supervisor", actor_id: "brain-llm",  event_type: "reflector_activated",   target_id: "register_adc",         payload: { reasoning: "Voxel grid mismatch detected. Will resample ADC to T2w space first.", attempt: 2 },             parent_event_id: "event-4" },
      { event_id: "event-6", graph_id: "graph-prostate-demo", ts: "2026-03-19T00:01:12Z", actor_type: "supervisor", actor_id: "brain-llm",  event_type: "patch_proposed",        target_id: "register_adc",         payload: { op: "prepend_step", step: "resample_to_reference_grid", params: { spacing: [0.5,0.5,1.0] } },             parent_event_id: "event-5" },
    ],
    proposals: [
      { patch_id: "patch-preview-1", graph_id: "graph-prostate-demo", author_type: "human", author_id: "demo-user", timestamp: "2026-03-19T00:00:08Z", reason: "Add a human review checkpoint before segmentation.", operations: [{ op: "insert_checkpoint", target: "segment_prostate", value: { after_node: "register_adc", title: "Review Registration Output", kind: "review" } }], applies_to_version: 1, result: "preview" },
    ],
    patch_history: [],
  },
};

// ── App State ────────────────────────────────────
const state = {
  connected: false,
  source: "fallback",
  session: clone(fallbackSnapshot.session),
  graph: clone(fallbackSnapshot.graph),
  events: clone(fallbackSnapshot.graph.events),
  toolCatalog: [],
  domainCatalog: {},
  capabilitySummary: { capabilities: [], tool_capabilities: {}, domain_capabilities: {} },
  bridgeHealth: null,
  plannerHealth: null,
  artifactPayloads: {},
  artifactLoading: {},
  selectedNodeId: "register_adc",
  selectedArtifactId: null,
  nodeLayouts: {},
  inspectorTab: "node",
  lastAction: "Loaded local fallback demo state.",
  reflectorState: { active: true, node_id: "register_adc", phase: "reflecting", attempt: 2, reasoning: "Detected voxel grid mismatch. Adding pre-resampling step and retrying." },
  reflectorDismissed: false,
};

// ── Layout constants ─────────────────────────────
const NODE_W  = 172;
const NODE_H  = 104;
const GAP_X   = 220;
const PAD     = 28;

// ── Utilities ────────────────────────────────────
function clone(v) { return JSON.parse(JSON.stringify(v)); }

function escapeHtml(v) {
  return String(v ?? "")
    .replaceAll("&","&amp;").replaceAll("<","&lt;")
    .replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;");
}

function escapeAttr(v) { return escapeHtml(v).replaceAll("`","&#96;"); }

function prettyJson(v) {
  if (v === null || v === undefined || v === "") return "null";
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

function truncate(v, n = 100) {
  const s = String(v ?? "");
  return s.length <= n ? s : s.slice(0, n-1).trimEnd() + "…";
}

function scalarPreview(v) {
  if (v === null)      return "null";
  if (v === undefined) return "undefined";
  if (Array.isArray(v)) { const h = v.slice(0,3).map(scalarPreview).join(", "); return v.length > 3 ? `[${h}, …] (${v.length})` : `[${h}]`; }
  if (typeof v === "object") { const k = Object.keys(v); return k.length ? `{${k.slice(0,3).join(", ")}${k.length>3?"…":""}}` : "{}"; }
  if (typeof v === "string") return truncate(v.replace(/\s+/g," ").trim(), 80) || '""';
  return String(v);
}

function svgToDataUri(s) { return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(String(s || ""))}`; }

function formatTs(ts) {
  if (!ts) return "";
  try { return new Date(ts).toLocaleTimeString([], { hour:"2-digit", minute:"2-digit", second:"2-digit" }); }
  catch { return ts; }
}

// ── Busy overlay ─────────────────────────────────
function setBusy(on, label = "Processing…") {
  const el = document.getElementById("busy-overlay");
  if (!el) return;
  el.classList.toggle("active", on);
  const lbl = el.querySelector(".busy-label");
  if (lbl) lbl.textContent = label;
}

// ── Fetch ─────────────────────────────────────────
async function fetchJson(path, opts) {
  const r = await fetch(API_BASE + path, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function fetchText(path, opts) {
  const url = /^https?:/.test(path) ? path : API_BASE + path;
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.text();
}

// ── Payload normalization ─────────────────────────
function unwrap(p, key) {
  if (!p || typeof p !== "object") return null;
  if (key && Object.prototype.hasOwnProperty.call(p, key)) return p[key];
  return p;
}
const normalizeSessionPayload = p => { const s = unwrap(p,"session"); return s && typeof s==="object" ? s : null; };
const normalizeGraphPayload   = p => { const g = unwrap(p,"graph");   return g && typeof g==="object" ? g : null; };
function normalizeEventsPayload(p) {
  const e = unwrap(p,"events");
  return Array.isArray(e) ? e : Array.isArray(p) ? p : [];
}
function normalizeJsonPayload(p) {
  if (p == null) return null;
  if (typeof p === "string") { try { return JSON.parse(p); } catch { return p; } }
  return p;
}

function ensureGraphDefaults(g) {
  if (!g) return g;
  ["nodes","edges","artifacts","events","proposals","patch_history"].forEach(k => { if (!Array.isArray(g[k])) g[k] = []; });
  return g;
}

// ── Layout ───────────────────────────────────────
function defaultNodeLayout(i) { return { x: PAD + i * GAP_X, y: PAD }; }

function ensureNodeLayouts(graph) {
  if (!graph?.nodes) return;
  const next = {};
  graph.nodes.forEach((n, i) => {
    const e = state.nodeLayouts?.[n.node_id];
    next[n.node_id] = (e && isFinite(e.x) && isFinite(e.y)) ? e : defaultNodeLayout(i);
  });
  state.nodeLayouts = next;
}

function graphMetrics(graph) {
  const pos = (graph?.nodes || []).map((n,i) => state.nodeLayouts?.[n.node_id] || defaultNodeLayout(i));
  return {
    width:  Math.max(700, pos.length ? Math.max(...pos.map(p=>p.x)) + NODE_W + PAD : PAD),
    height: Math.max(200, pos.length ? Math.max(...pos.map(p=>p.y)) + NODE_H + PAD + 40 : PAD),
  };
}

function portRight(nodeId) { const l = state.nodeLayouts?.[nodeId] || defaultNodeLayout(0); return { x: l.x + NODE_W, y: l.y + NODE_H/2 }; }
function portLeft(nodeId)  { const l = state.nodeLayouts?.[nodeId] || defaultNodeLayout(0); return { x: l.x,         y: l.y + NODE_H/2 }; }

// ── Artifact helpers ──────────────────────────────
function artifactPublicUrl(a) {
  const uri = String(a?.uri || "").trim();
  if (!uri) return null;
  if (/^(https?:|data:|blob:)/.test(uri)) return uri;
  if (uri.startsWith("/")) return uri;
  return `/artifacts/${uri.replace(/^artifacts\//,"").replace(/^\/+/,"")}`;
}

function extractInline(a) {
  if (!a || typeof a !== "object") return null;
  for (const c of [a.preview,a.content,a.text,a.data,a.body,a.value,a.payload,a.source,a.metadata?.preview,a.metadata?.content,a.metadata?.text,a.metadata?.data]) {
    if (c !== undefined && c !== null && c !== "") return c;
  }
  return null;
}

function extractPayload(a) {
  if (!a || typeof a !== "object") return null;
  const cached = a.artifact_id ? state.artifactPayloads?.[a.artifact_id] : undefined;
  if (cached !== undefined && cached !== null && cached !== "") return cached;
  return extractInline(a);
}

function inferFormat(a, payload) {
  const kind = String(a?.kind||"").toLowerCase(), mime = String(a?.mime_type||"").toLowerCase(), uri = String(a?.uri||"").toLowerCase();
  const pt = Array.isArray(payload) ? "array" : payload===null ? "null" : typeof payload;
  const txt = typeof payload==="string" ? payload.trim() : typeof payload?.svg==="string" ? payload.svg.trim() : typeof payload?.markup==="string" ? payload.markup.trim() : null;
  if (txt && (txt.startsWith("data:image/svg+xml") || txt.includes("<svg"))) return "svg";
  if (kind==="svg" || mime.includes("image/svg") || uri.endsWith(".svg")) return "svg";
  if (kind==="json" || mime.includes("json") || uri.endsWith(".json") || pt==="object" || pt==="array") return "json";
  if (kind==="text"||kind==="report"||kind==="log"||mime.startsWith("text/")||[".txt",".md",".log",".csv"].some(e=>uri.endsWith(e))) return "text";
  if (mime.startsWith("image/") || ["png","jpg","jpeg","gif","webp"].includes(kind)) return "image";
  return "metadata";
}

function artifactById(id) { return (state.graph.artifacts||[]).find(a=>a.artifact_id===id)||null; }
function collectArtifacts(nodeId) { return (state.graph.artifacts||[]).filter(a=>a.node_id===nodeId); }
function nodeById(nodeId) { return state.graph.nodes.find(n=>n.node_id===nodeId)||null; }

function pruneArtifactCaches() {
  const valid = new Set((state.graph?.artifacts||[]).map(a=>a.artifact_id));
  state.artifactPayloads = Object.fromEntries(Object.entries(state.artifactPayloads||{}).filter(([id])=>valid.has(id)));
  state.artifactLoading  = Object.fromEntries(Object.entries(state.artifactLoading ||{}).filter(([id])=>valid.has(id)));
}

function selectFirstArtifactForNode(nodeId) {
  const v = collectArtifacts(nodeId).filter(a=>a.visible!==false);
  if (v.length) return v[0].artifact_id;
  return (state.graph.artifacts||[]).find(a=>a.visible!==false)?.artifact_id || null;
}

function syncArtifactSelection() {
  const node = nodeById(state.selectedNodeId) || state.graph.nodes[0] || null;
  const preferred = node ? selectFirstArtifactForNode(node.node_id) : null;
  const nodeArts = node ? collectArtifacts(node.node_id).filter(a=>a.visible!==false) : [];
  const sessionSel = state.session?.case_state?.selected_artifacts || [];
  const match = sessionSel.find(id=>nodeArts.some(a=>a.artifact_id===id));
  const avail = new Set((state.graph.artifacts||[]).filter(a=>a.visible!==false).map(a=>a.artifact_id));
  const next = match || (preferred && avail.has(preferred) ? preferred : null) || null;
  if (next !== state.selectedArtifactId) state.selectedArtifactId = next;
  if (state.selectedArtifactId && !avail.has(state.selectedArtifactId)) state.selectedArtifactId = null;
}

function selectArtifact(id) {
  state.selectedArtifactId = id;
  const a = artifactById(id);
  if (a) {
    state.session.case_state.selected_artifacts = [id];
    state.session.case_state.ui_focus = {...(state.session.case_state.ui_focus||{}), panel:"viewer", selected_artifact:id};
    void ensureArtifactPayloadLoaded(a);
  }
}

async function ensureArtifactPayloadLoaded(a) {
  if (!a?.artifact_id || extractInline(a)!==null) return;
  if (Object.prototype.hasOwnProperty.call(state.artifactPayloads, a.artifact_id) || state.artifactLoading?.[a.artifact_id]) return;
  const fmt = inferFormat(a, null);
  if (!["json","text","svg"].includes(fmt)) return;
  const url = artifactPublicUrl(a);
  if (!url) return;
  state.artifactLoading = {...(state.artifactLoading||{}), [a.artifact_id]: true};
  try {
    const text = await fetchText(url);
    state.artifactPayloads = {...(state.artifactPayloads||{}), [a.artifact_id]: fmt==="json" ? normalizeJsonPayload(text) : text};
  } catch(err) {
    console.error(err);
    state.artifactPayloads = {...(state.artifactPayloads||{}), [a.artifact_id]: null};
  } finally {
    const nxt = {...(state.artifactLoading||{})};
    delete nxt[a.artifact_id];
    state.artifactLoading = nxt;
    renderAll();
  }
}

// ── Snapshot ──────────────────────────────────────
function applySnapshot(snap, source) {
  const session = normalizeSessionPayload(snap.session || snap);
  const graph   = normalizeGraphPayload(snap.graph || snap?.graph || session?.graph);
  const events  = normalizeEventsPayload(snap.events || graph?.events || session?.graph?.events);
  state.connected = source === "api";
  state.source    = source;
  state.session   = clone(session || fallbackSnapshot.session);
  state.graph     = ensureGraphDefaults(clone(graph || fallbackSnapshot.graph));
  ensureNodeLayouts(state.graph);
  state.events    = clone(events || []);
  if (!state.selectedNodeId || !state.graph.nodes.some(n=>n.node_id===state.selectedNodeId)) {
    state.selectedNodeId = state.graph.nodes[0]?.node_id || null;
  }
  state.session.graph        = state.graph;
  state.session.chat_history = state.session.chat_history || [];
  state.session.tool_catalog = state.toolCatalog;
  state.session.capabilities = state.capabilitySummary?.capabilities || [];
  pruneArtifactCaches();
  syncArtifactSelection();
}

// ══════════════════════════════════════════════════
//  RENDER FUNCTIONS
// ══════════════════════════════════════════════════

function renderHealth() {
  const badge = document.getElementById("health-badge");
  if (badge) {
    const dot = badge.querySelector(".conn-dot");
    const lbl = badge.querySelector("#conn-label");
    badge.dataset.state = state.connected ? "connected" : "offline";
    if (dot) dot.className = "conn-dot";
    if (lbl) lbl.textContent = state.connected ? "connected" : "demo mode";
  }
  const src = document.getElementById("source-badge");
  if (src) src.textContent = state.source === "api" ? "live" : "fallback";
}

function renderSummaryStrip() {
  const strip = document.getElementById("summary-strip");
  if (!strip) return;
  const cs = state.session.case_state;
  const g  = state.graph;
  const done  = (g.nodes||[]).filter(n=>n.status==="succeeded").length;
  const total = (g.nodes||[]).length;
  const pct   = total ? Math.round(done/total*100) : 0;

  // Update progress bar
  const fill  = document.getElementById("progress-fill");
  const plbl  = document.getElementById("progress-label");
  if (fill) fill.style.width = pct + "%";
  if (plbl) plbl.textContent = `${done}/${total}`;

  const chips = [
    { k: "Case",   v: cs.case_id },
    { k: "Domain", v: cs.domain  },
    { k: "Status", v: g.status || "—", accent: true },
    state.plannerHealth?.status ? { k: "Brain",  v: state.plannerHealth.status } : null,
    state.bridgeHealth?.status  ? { k: "Bridge", v: state.bridgeHealth.status  } : null,
  ].filter(Boolean);

  strip.innerHTML = chips.map(c => `
    <div class="case-chip${c.accent?" accent":""}">
      <span class="chip-label">${escapeHtml(c.k)}</span>
      <span class="chip-value">${escapeHtml(String(c.v))}</span>
    </div>
  `).join("");
}

// ── Chat ──────────────────────────────────────────
function renderChat() {
  const el = document.getElementById("chat-history");
  if (!el) return;
  const hist = state.session.chat_history || [];
  if (!hist.length) {
    el.innerHTML = `<div class="empty-state" style="margin-top:24px"><p>No messages yet. Type below to begin.</p></div>`;
    return;
  }
  el.innerHTML = hist.map(m => {
    const roleLabel = m.role === "user" ? "You" : m.role === "reflector" ? "⟳ Brain Reflector" : "Workstation AI";
    return `<div class="message ${escapeHtml(m.role)}">
      <div class="msg-role">${escapeHtml(roleLabel)}</div>
      <div class="msg-bubble">${escapeHtml(m.content)}</div>
    </div>`;
  }).join("");
  el.scrollTop = el.scrollHeight;
}

// ── Pipeline strip (compact node tracker in graph header) ─
function renderPipelineStrip() {
  const el = document.getElementById("pipeline-strip");
  if (!el) return;
  const nodes = state.graph.nodes || [];
  if (!nodes.length) { el.innerHTML = ""; return; }
  let html = "";
  nodes.forEach((n, i) => {
    const sel = n.node_id === state.selectedNodeId;
    const cls = [n.status || "planned", sel ? "selected" : ""].filter(Boolean).join(" ");
    html += `
      <div class="pip-step">
        <div class="pip-dot ${cls}" data-node-id="${escapeAttr(n.node_id)}" title="${escapeAttr(n.title)} (${escapeAttr(n.status||'planned')})">
          <div class="pip-dot-inner"></div>
          <span class="pip-label">${escapeHtml(truncate(n.title, 12))}</span>
        </div>
        ${i < nodes.length-1 ? `<div class="pip-line${n.status==="succeeded"?" done":""}"></div>` : ""}
      </div>
    `;
  });
  el.innerHTML = html;

  el.querySelectorAll(".pip-dot[data-node-id]").forEach(dot => {
    dot.addEventListener("click", () => { state.selectedNodeId = dot.dataset.nodeId; renderAll(); });
  });
}

// ── Graph canvas ──────────────────────────────────
function renderGraph() {
  const canvas = document.getElementById("graph-canvas");
  if (!canvas) return;
  const graph = state.graph;
  const m = graphMetrics(graph);

  const nodesHtml = (graph.nodes||[]).map((n, i) => {
    const l   = state.nodeLayouts?.[n.node_id] || defaultNodeLayout(i);
    const sel = n.node_id === state.selectedNodeId;
    const st  = n.status || "planned";
    const tool = n.tool_name || n.action_type || "";
    return `
      <div class="graph-node status-${escapeHtml(st)}${sel?" selected":""}"
        data-node-id="${escapeAttr(n.node_id)}"
        style="left:${l.x}px;top:${l.y}px;width:${NODE_W}px"
        role="button" tabindex="0" aria-label="${escapeAttr(n.title)} — ${escapeAttr(st)}">
        <div class="node-bar status-${escapeHtml(st)}"></div>
        <div class="node-content">
          <div class="node-top-row">
            <span class="node-kind">${escapeHtml(n.kind)}</span>
            <span class="spill ${escapeHtml(st)}">${escapeHtml(st)}</span>
          </div>
          <div class="node-title">${escapeHtml(n.title)}</div>
          ${tool ? `<div class="node-tool-name">${escapeHtml(tool)}</div>` : ""}
        </div>
        <div class="node-footer">
          <span class="node-owner">${escapeHtml(n.owner||"")}</span>
          ${n.editable ? `<span class="chip" style="font-size:8.5px;padding:1px 5px">editable</span>` : ""}
        </div>
      </div>
    `;
  }).join("");

  const edgesHtml = (graph.edges||[]).map(e => {
    const from = portRight(e.from_node);
    const to   = portLeft(e.to_node);
    const dx   = Math.abs(to.x - from.x) * 0.45;
    const d    = `M${from.x},${from.y} C${from.x+dx},${from.y} ${to.x-dx},${to.y} ${to.x},${to.y}`;
    return `<path class="g-edge type-${escapeHtml(e.type||"control")}" data-edge-id="${escapeAttr(e.edge_id)}" d="${d}" marker-end="url(#arr)"/>`;
  }).join("");

  canvas.innerHTML = `
    <div class="graph-stage" style="width:${m.width}px;height:${m.height}px;position:relative;">
      <svg class="graph-edges" width="${m.width}" height="${m.height}" style="position:absolute;inset:0;overflow:visible;pointer-events:none;">
        <defs>
          <marker id="arr" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,7 L7,3.5 z" fill="rgba(37,66,104,0.9)"/>
          </marker>
        </defs>
        ${edgesHtml}
      </svg>
      ${nodesHtml}
    </div>
  `;

  // Graph meta
  const meta = document.getElementById("graph-meta");
  if (meta) {
    const st  = graph.status || "—";
    const done = (graph.nodes||[]).filter(n=>n.status==="succeeded").length;
    meta.innerHTML = `
      <span class="graph-meta-pill">${escapeHtml(st)}</span>
      <span class="graph-meta-pill">v${escapeHtml(String(graph.version||1))}</span>
      <span class="graph-meta-pill">${done}/${(graph.nodes||[]).length} done</span>
    `;
  }

  // Interactions
  canvas.querySelectorAll(".graph-node").forEach(el => {
    const nodeId = el.dataset.nodeId;
    el.addEventListener("click", e => { if (!e.defaultPrevented) { state.selectedNodeId = nodeId; renderAll(); } });
    el.addEventListener("keydown", e => { if (e.key==="Enter"||e.key===" ") { e.preventDefault(); state.selectedNodeId=nodeId; renderAll(); } });
    attachDrag(nodeId, el);
  });

  renderPipelineStrip();
}

function updateGraphGeometry() {
  const canvas = document.getElementById("graph-canvas");
  const stage  = canvas?.querySelector(".graph-stage");
  if (!canvas || !stage) return;
  const m = graphMetrics(state.graph);
  stage.style.width  = `${m.width}px`;
  stage.style.height = `${m.height}px`;
  const svg = stage.querySelector(".graph-edges");
  if (svg) { svg.setAttribute("width",String(m.width)); svg.setAttribute("height",String(m.height)); }
  (state.graph.nodes||[]).forEach((n,i) => {
    const el = stage.querySelector(`[data-node-id="${CSS.escape(n.node_id)}"]`);
    const l  = state.nodeLayouts?.[n.node_id] || defaultNodeLayout(i);
    if (el) { el.style.left=`${l.x}px`; el.style.top=`${l.y}px`; }
  });
  (state.graph.edges||[]).forEach(e => {
    const path = stage.querySelector(`[data-edge-id="${CSS.escape(e.edge_id)}"]`);
    if (!path) return;
    const from = portRight(e.from_node), to = portLeft(e.to_node);
    const dx = Math.abs(to.x-from.x)*0.45;
    path.setAttribute("d",`M${from.x},${from.y} C${from.x+dx},${from.y} ${to.x-dx},${to.y} ${to.x},${to.y}`);
  });
}

function attachDrag(nodeId, handle) {
  handle.addEventListener("pointerdown", ev => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    const origin = state.nodeLayouts?.[nodeId] || defaultNodeLayout(0);
    const sx = ev.clientX, sy = ev.clientY;
    let moved = false;
    const onMove = e => {
      moved = true;
      state.nodeLayouts = {...(state.nodeLayouts||{}), [nodeId]: {x:Math.max(8,origin.x+(e.clientX-sx)), y:Math.max(8,origin.y+(e.clientY-sy))}};
      updateGraphGeometry();
    };
    const onUp = e => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (moved) e.preventDefault();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, {once:true});
  });
}

// ── Viewer ────────────────────────────────────────
function renderViewer() {
  renderArtifactRail();
  renderArtifactPreview();
}

function renderArtifactRail() {
  const el = document.getElementById("artifact-list");
  if (!el) return;
  const arts = (state.graph.artifacts||[]).filter(a=>a.visible!==false);
  if (!arts.length) {
    el.innerHTML = `<p style="font-size:11.5px;color:var(--muted);text-align:center;padding:14px 0">No artifacts yet.</p>`;
    return;
  }
  el.innerHTML = arts.map(a => {
    const sel = a.artifact_id === state.selectedArtifactId;
    const fmt = inferFormat(a, extractPayload(a));
    return `
      <button class="art-item${sel?" selected":""}" type="button" data-artifact-id="${escapeAttr(a.artifact_id)}">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:5px">
          <span class="art-name">${escapeHtml(a.name||a.artifact_id)}</span>
          <span class="spill ${sel?"ready":"planned"}" style="font-size:8.5px;flex-shrink:0">${escapeHtml(fmt)}</span>
        </div>
        <div class="art-uri">${escapeHtml(a.uri||"no uri")}</div>
      </button>
    `;
  }).join("");
  el.querySelectorAll(".art-item").forEach(btn => {
    btn.addEventListener("click", () => { selectArtifact(btn.dataset.artifactId); renderAll(); });
  });
}

function renderArtifactPreview() {
  const el = document.getElementById("artifact-preview");
  if (!el) return;
  const a = artifactById(state.selectedArtifactId);
  if (!a) {
    el.innerHTML = `
      <div class="empty-state" style="flex:1;min-height:120px">
        <svg width="28" height="28" viewBox="0 0 28 28" fill="none"><rect x="4" y="4" width="20" height="20" rx="3" stroke="currentColor" stroke-width="1.5"/><line x1="8" y1="10" x2="20" y2="10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="8" y1="14" x2="16" y2="14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="8" y1="18" x2="12" y2="18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
        <p>Select a node, then choose an artifact to preview.</p>
      </div>
    `;
    return;
  }
  el.innerHTML = buildPreviewHtml(a);
}

function buildPreviewHtml(a) {
  const payload = extractPayload(a);
  const fmt     = inferFormat(a, payload);
  const norm    = normalizeJsonPayload(payload);
  const disp    = norm !== null ? norm : payload;
  const loading = Boolean(state.artifactLoading?.[a.artifact_id]);
  const pubUrl  = artifactPublicUrl(a);

  const chips = [a.kind, a.role, a.mime_type].filter(Boolean).map(c=>`<span class="chip">${escapeHtml(c)}</span>`).join("");
  const header = `
    <div class="prev-head">
      <div class="row">
        <div>
          <div class="eyebrow" style="margin-bottom:3px">Artifact</div>
          <h3>${escapeHtml(a.name||a.artifact_id)}</h3>
          <div class="prev-path">${escapeHtml(a.uri||"no uri")}</div>
        </div>
        ${pubUrl ? `<a class="prev-link" href="${escapeAttr(pubUrl)}" target="_blank" rel="noreferrer" title="Open raw">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M5 2H2v8h8V7M7 1h4v4M5 7l5-5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>Raw
        </a>` : ""}
      </div>
      ${chips ? `<div class="chip-row">${chips}</div>` : ""}
      <div class="meta-row">
        <div class="meta-cell"><div class="mc-label">ID</div><div class="mc-val" style="font-family:var(--font-mono);font-size:9.5px;word-break:break-all">${escapeHtml(a.artifact_id)}</div></div>
        <div class="meta-cell"><div class="mc-label">Node</div><div class="mc-val">${escapeHtml(a.node_id)}</div></div>
        <div class="meta-cell"><div class="mc-label">Format</div><div class="mc-val">${escapeHtml(fmt)}</div></div>
      </div>
    </div>
  `;

  if (fmt === "svg") {
    const svg = typeof disp==="string" ? disp : disp?.svg || disp?.markup || "";
    const src = svg ? (svg.startsWith("data:")? svg : svgToDataUri(svg)) : pubUrl;
    return `${header}
      <div class="prev-stage">${src ? `<img class="artifact-svg" src="${src}" alt="${escapeAttr(a.name)}"/>` : loading ? `<p style="color:var(--muted);font-size:12px">Loading…</p>` : `<p style="color:var(--muted);font-size:12px">SVG unavailable.</p>`}</div>
      ${svg ? `<details class="prev-details"><summary>SVG source</summary><div class="code">${escapeHtml(svg)}</div></details>` : ""}`;
  }

  if (fmt === "json") {
    return `${header}
      ${loading && !disp ? `<p style="color:var(--muted);font-size:12px">Loading…</p>` : ""}
      ${buildPrevGrid(norm && typeof norm==="object" ? norm : {})}
      <details class="prev-details" open><summary>JSON payload</summary><div class="code">${escapeHtml(prettyJson(disp))}</div></details>`;
  }

  if (fmt === "text") {
    const txt = typeof disp==="string" ? disp : prettyJson(disp);
    const lines = txt.split(/\r?\n/);
    return `${header}
      <div class="prev-text-chips chip-row">
        <span class="chip">${lines.length} lines</span>
        <span class="chip">${escapeHtml(truncate(txt.replace(/\s+/g," "),60))}</span>
      </div>
      <pre class="code prev-lines">${escapeHtml(lines.slice(0,30).map((l,i)=>`${String(i+1).padStart(2,"0")}  ${l}`).join("\n"))}${lines.length>30?"\n…":"" }</pre>`;
  }

  if (fmt === "image") {
    const src = typeof disp==="string" ? disp : disp?.uri||disp?.src||pubUrl;
    return `${header}<div class="prev-stage">${src ? `<img class="artifact-image" src="${escapeAttr(src)}" alt="${escapeAttr(a.name)}"/>` : `<p style="color:var(--muted);font-size:12px">Image unavailable.</p>`}</div>`;
  }

  const meta = a.metadata || {};
  return `${header}
    ${Object.keys(meta).length ? buildPrevGrid(meta) : `<p style="color:var(--muted);font-size:12px;margin:4px 0">No preview data.</p>`}
    <details class="prev-details" open><summary>Metadata</summary><div class="code">${escapeHtml(prettyJson(meta))}</div></details>`;
}

function buildPrevGrid(val, limit=6) {
  if (!val || typeof val !== "object") return "";
  const entries = Array.isArray(val) ? val.slice(0,limit).map((v,i)=>[String(i),v]) : Object.entries(val).slice(0,limit);
  if (!entries.length) return `<p style="color:var(--muted);font-size:12px">Empty.</p>`;
  return `<div class="prev-grid">${entries.map(([k,v])=>`<div class="prev-item"><div class="pi-key">${escapeHtml(k)}</div><div class="pi-val">${escapeHtml(scalarPreview(v))}</div></div>`).join("")}</div>`;
}

// ── Inspector ─────────────────────────────────────
function renderInspector() {
  const el = document.getElementById("inspector");
  if (!el) return;
  switch (state.inspectorTab) {
    case "node":   el.innerHTML = buildNodeTab();   break;
    case "events": el.innerHTML = buildEventsTab(); break;
    case "tools":  el.innerHTML = buildToolsTab();  break;
    default:       el.innerHTML = buildNodeTab();
  }
  el.querySelectorAll("[data-artifact-id]").forEach(btn => {
    btn.addEventListener("click", () => { selectArtifact(btn.dataset.artifactId); renderAll(); });
  });
}

function collectRelatedEvents(nodeId) {
  return (state.events||[]).filter(e=>e.target_id===nodeId||e.payload?.node_id===nodeId||e.payload?.selected_node===nodeId);
}

function buildNodeTab() {
  const node = nodeById(state.selectedNodeId);
  if (!node) return `<div class="empty-state"><p>Click any node on the pipeline to inspect it.</p></div>`;

  const arts  = collectArtifacts(node.node_id);
  const evts  = collectRelatedEvents(node.node_id);
  const props = state.graph.proposals || [];

  // ── Error + Reflector section (top priority when failed) ──
  const isFailed     = node.status === "failed" || node.status === "reflecting" || node.status === "retrying";
  const retryHistory = node.retry_history || [];
  const rs = state.reflectorState;

  const errorSection = isFailed && node.last_error ? `
    <div class="insp-section">
      <div class="sec-label">⚠ Error Details</div>
      <div class="error-block">
        <div class="error-block-title">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="5" stroke="#FF8080" stroke-width="1.2"/><line x1="6" y1="3.5" x2="6" y2="6.5" stroke="#FF8080" stroke-width="1.2" stroke-linecap="round"/><circle cx="6" cy="8.5" r="0.6" fill="#FF8080"/></svg>
          Runtime Error
        </div>
        <div class="error-block-msg">${escapeHtml(node.last_error)}</div>
        ${node.error_ts ? `<div style="font-family:var(--font-mono);font-size:9px;color:var(--muted);margin-top:2px">${escapeHtml(formatTs(node.error_ts))}</div>` : ""}
      </div>
    </div>` : "";

  const reflectorSection = (isFailed && (node.reflector_reasoning || rs?.reasoning)) ? `
    <div class="insp-section">
      <div class="sec-label">🧠 Brain LLM Analysis</div>
      <div class="reflector-block">
        <div class="reflector-block-title">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M6 1C3.24 1 1 3.24 1 6s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zm0 2c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1zm0 7c-1.38 0-2.63-.7-3.36-1.76.02-.87 2.24-1.34 3.36-1.34s3.34.47 3.36 1.34C8.63 9.3 7.38 10 6 10z" fill="#FFAB6A"/></svg>
          Reflector Reasoning
        </div>
        <div class="reflector-block-msg">${escapeHtml(node.reflector_reasoning || rs?.reasoning || "")}</div>
      </div>
    </div>` : "";

  const retrySection = retryHistory.length ? `
    <div class="insp-section">
      <div class="sec-label">Retry History</div>
      <div class="retry-history">
        ${retryHistory.map(r => `
          <div class="retry-entry">
            <span class="re-n">#${escapeHtml(String(r.attempt))}</span>
            <span class="re-status"><span class="spill ${escapeHtml(r.status)}">${escapeHtml(r.status)}</span></span>
            <span class="re-msg">${escapeHtml(r.error || "OK")}</span>
            <span class="re-time">${escapeHtml(formatTs(r.ts))}</span>
          </div>`).join("")}
      </div>
    </div>` : "";

  const artsHtml = arts.length
    ? arts.map(a => {
        const sel = a.artifact_id===state.selectedArtifactId;
        const fmt = inferFormat(a, extractPayload(a));
        return `<button class="insp-art-card${sel?" selected":""}" type="button" data-artifact-id="${escapeAttr(a.artifact_id)}">
          <div class="row"><span style="font-size:12px;font-weight:600;color:var(--text-strong)">${escapeHtml(a.name||a.artifact_id)}</span><span class="spill ${sel?"ready":"planned"}" style="font-size:8.5px">${escapeHtml(fmt)}</span></div>
          <div class="art-uri">${escapeHtml(a.uri||"")}</div>
        </button>`;
      }).join("")
    : `<p style="font-size:12px;color:var(--muted)">No artifacts on this node.</p>`;

  const propsHtml = props.length ? props.map(p=>`
    <div class="proposal-card">
      <div class="row"><strong style="font-size:12px;color:var(--text-strong)">${escapeHtml(p.reason)}</strong><span class="spill planned" style="font-size:8.5px">${escapeHtml(p.result||"preview")}</span></div>
      <div style="font-family:var(--font-mono);font-size:9.5px;color:var(--muted)">${escapeHtml(p.patch_id)} · v${escapeHtml(String(p.applies_to_version))}</div>
    </div>`).join("") : "";

  return `
    ${errorSection}
    ${reflectorSection}
    ${retrySection}
    <div class="insp-section">
      <div class="sec-label">Node</div>
      <div class="kv">
        <span class="kv-k">ID</span>     <span class="kv-v mono">${escapeHtml(node.node_id)}</span>
        <span class="kv-k">Kind</span>   <span class="kv-v"><span class="node-kind">${escapeHtml(node.kind)}</span></span>
        <span class="kv-k">Status</span> <span class="kv-v"><span class="spill ${escapeHtml(node.status||"planned")}">${escapeHtml(node.status||"planned")}</span></span>
        <span class="kv-k">Owner</span>  <span class="kv-v mono">${escapeHtml(node.owner||"—")}</span>
        <span class="kv-k">Tool</span>   <span class="kv-v mono">${escapeHtml(node.tool_name||"—")}</span>
      </div>
      ${node.notes ? `<p style="font-size:12px;color:var(--text-dim);margin-top:2px">${escapeHtml(node.notes)}</p>` : ""}
    </div>

    ${(node.depends_on||[]).length ? `
    <div class="insp-section">
      <div class="sec-label">Dependencies</div>
      <div class="chip-row">${(node.depends_on||[]).map(d=>`<span class="chip">${escapeHtml(d)}</span>`).join("")}</div>
    </div>` : ""}

    ${(node.checks||[]).length ? `
    <div class="insp-section">
      <div class="sec-label">Checks</div>
      <div class="chip-row">${(node.checks||[]).map(c=>`<span class="chip">✓ ${escapeHtml(c)}</span>`).join("")}</div>
    </div>` : ""}

    <div class="insp-section">
      <div class="sec-label">Inputs</div>
      ${Object.keys(node.inputs||{}).length
        ? `<div class="code" style="font-size:10px">${escapeHtml(prettyJson(node.inputs))}</div>`
        : `<p style="font-size:12px;color:var(--muted)">—</p>`}
    </div>

    <div class="insp-section">
      <div class="sec-label">Outputs</div>
      ${Object.keys(node.outputs||{}).length
        ? `<div class="code" style="font-size:10px">${escapeHtml(prettyJson(node.outputs))}</div>`
        : `<p style="font-size:12px;color:var(--muted)">—</p>`}
    </div>

    <div class="insp-section">
      <div class="sec-label">Artifacts (${arts.length})</div>
      ${artsHtml}
    </div>

    ${propsHtml ? `<div class="insp-section"><div class="sec-label">Pending Proposals</div>${propsHtml}</div>` : ""}

    ${evts.length ? `
    <div class="insp-section">
      <div class="sec-label">Recent Events</div>
      ${evts.slice(-3).map(e=>`
        <div class="event-card">
          <div class="ev-type">${escapeHtml(e.event_type)}</div>
          <div class="ev-actor">${escapeHtml(e.actor_type)}:${escapeHtml(e.actor_id)}</div>
          <div class="ev-time">${escapeHtml(formatTs(e.ts))}</div>
        </div>`).join("")}
    </div>` : ""}
  `;
}

function buildEventsTab() {
  const evts = [...(state.events||[])].reverse();
  const last = state.session?.case_state?.last_event_id;
  if (!evts.length) return `<div class="empty-state"><p>No events recorded yet.</p></div>`;
  return `
    <div class="insp-section">
      <div class="sec-label">Timeline — ${evts.length} events</div>
      ${evts.map(e=>`
        <div class="event-card${e.event_id===last?" active":""}">
          <div class="ev-type">${escapeHtml(e.event_type)}</div>
          <div class="ev-actor">${escapeHtml(e.actor_type)}:${escapeHtml(e.actor_id)} → ${escapeHtml(e.target_id)}</div>
          <div class="ev-time">${escapeHtml(formatTs(e.ts))}</div>
          ${Object.keys(e.payload||{}).length
            ? `<details class="prev-details" style="margin-top:3px"><summary style="font-size:10.5px">Payload</summary><div class="code" style="font-size:9.5px">${escapeHtml(prettyJson(e.payload))}</div></details>` : ""}
        </div>`).join("")}
    </div>`;
}

function buildToolsTab() {
  const caps  = deriveCapabilities();
  const planner = state.plannerHealth;
  const bridge  = state.bridgeHealth;
  const mods    = state.session?.case_state?.available_modalities || [];

  const healthHtml = `
    <div class="insp-section">
      <div class="sec-label">Runtime</div>
      <div class="kv">
        <span class="kv-k">Brain</span>  <span class="kv-v"><span class="spill ${planner?.status==="ok"?"succeeded":"planned"}">${escapeHtml(planner?.status||"unknown")}</span></span>
        <span class="kv-k">Bridge</span> <span class="kv-v"><span class="spill ${bridge?.status==="ok"?"succeeded":"planned"}">${escapeHtml(bridge?.status||"unknown")}</span></span>
        ${planner?.mode ? `<span class="kv-k">Mode</span><span class="kv-v mono">${escapeHtml(planner.mode)}</span>` : ""}
      </div>
    </div>
    ${mods.length ? `<div class="insp-section"><div class="sec-label">Modalities</div><div class="chip-row">${mods.map(m=>`<span class="chip">${escapeHtml(m)}</span>`).join("")}</div></div>` : ""}
  `;

  if (!caps.length) {
    return healthHtml + `<div class="empty-state"><p>Connect to backend to see the tool catalog.</p></div>`;
  }

  return healthHtml + `
    <div class="insp-section">
      <div class="sec-label">Capabilities (${caps.length})</div>
      ${caps.map(c=>{
        const statusChips = Object.entries(c.status_summary||{}).map(([s,n])=>`<span class="chip" style="font-size:8.5px">${escapeHtml(s)}:${n}</span>`).join("");
        return `
          <div class="tool-card">
            <div class="row">
              <strong>${escapeHtml(c.title||c.name||c.tool_name||c.capability_id)}</strong>
              <span class="spill planned" style="font-size:8.5px">${escapeHtml(c.node_count?`${c.node_count}×`:"catalog")}</span>
            </div>
            ${c.description||c.action_types?.length ? `<p>${escapeHtml(c.description||(c.action_types||[]).join(", "))}</p>` : ""}
            ${statusChips ? `<div class="chip-row" style="margin-top:3px">${statusChips}</div>` : ""}
          </div>`;
      }).join("")}
    </div>`;
}

// ── Capability derivation (unchanged) ────────────
function normalizeCapabilitySource(src) {
  const raw = Array.isArray(src) ? src : Array.isArray(src?.tools) ? src.tools : Array.isArray(src?.capabilities) ? src.capabilities : [];
  return raw.map((item, i) => {
    if (typeof item === "string") return {capability_id:`cap-${i+1}`,title:item,name:item,source:"backend"};
    if (!item||typeof item!=="object") return null;
    const name = item.name||item.title||item.tool_name||item.action_type||item.capability_id||`cap-${i+1}`;
    return {
      capability_id: item.capability_id||item.id||`cap-${i+1}`,
      title: item.title||name, name,
      tool_name: item.tool_name||item.name||null,
      action_types: Array.isArray(item.action_types) ? item.action_types : item.action_type ? [item.action_type] : [],
      node_ids:   Array.isArray(item.node_ids)   ? item.node_ids   : [],
      owners:     (Array.isArray(item.owners)?item.owners:item.owner?[item.owner]:[]).filter(Boolean),
      description: item.description||item.summary||"",
      domains: Array.isArray(item.domains) ? item.domains : [],
      source:  item.source||"backend",
    };
  }).filter(Boolean);
}

function deriveCapabilitiesFromGraph(graph) {
  const map = new Map();
  (graph?.nodes||[]).forEach(n => {
    const key = n.tool_name||n.action_type||n.kind;
    if (!key) return;
    const e = map.get(key) || {capability_id:`derived-${key}`,title:key,name:key,tool_name:n.tool_name||null,action_types:new Set(),node_ids:[],owners:new Set(),statuses:{},inputs:{},outputs:{},source:"derived"};
    e.action_types.add(n.action_type||n.kind||key);
    e.node_ids.push(n.node_id);
    e.owners.add(n.owner||"supervisor");
    e.statuses[n.status||"planned"] = (e.statuses[n.status||"planned"]||0)+1;
    if (!Object.keys(e.inputs).length)  e.inputs  = n.inputs  || {};
    if (!Object.keys(e.outputs).length) e.outputs = n.outputs || {};
    map.set(key, e);
  });
  return Array.from(map.values()).map(e=>({...e,action_types:Array.from(e.action_types),owners:Array.from(e.owners),node_count:e.node_ids.length,status_summary:e.statuses})).sort((a,b)=>b.node_count-a.node_count);
}

function deriveCapabilities() {
  for (const src of [state.toolCatalog,state.session?.capabilities,state.graph?.capabilities,state.session?.tool_catalog]) {
    const n = normalizeCapabilitySource(src);
    if (n.length) return n;
  }
  return deriveCapabilitiesFromGraph(state.graph);
}

// ── Execution helpers ─────────────────────────────
function topologicalOrder(g) { return g.nodes.slice(); }
function isRunnable(n, byId) {
  if (!n || n.status==="succeeded" || n.status==="running") return false;
  return (n.depends_on||[]).map(d=>byId.get(d)).filter(Boolean).every(u=>u.status==="succeeded");
}
function nextRunnableNode() {
  const byId = new Map(state.graph.nodes.map(n=>[n.node_id,n]));
  return state.graph.nodes.find(n=>n.status==="running") || topologicalOrder(state.graph).find(n=>isRunnable(n,byId)) || null;
}

function advanceExecution() {
  const next = nextRunnableNode();
  if (!next) { state.lastAction = "No runnable node found."; renderAll(); return; }
  next.status = "running";
  state.selectedNodeId = next.node_id;
  renderAll();
  setTimeout(() => {
    const n = nodeById(next.node_id);
    if (n) { n.status = "succeeded"; state.lastAction = `Local: executed ${next.node_id}.`; }
    renderAll();
  }, 900);
}

function advanceExecutionUntilDone(maxSteps = 20) {
  let steps = 0;
  while (steps < maxSteps) {
    const next = nextRunnableNode();
    if (!next) break;
    next.status = "succeeded";
    state.selectedNodeId = next.node_id;
    state.lastAction = `Local: executed ${next.node_id}.`;
    steps += 1;
  }
  renderAll();
}

function applyLatestProposal() {
  const ps = state.graph.proposals || [];
  if (!ps.length) { state.lastAction = "No proposals to apply."; renderAll(); return; }
  const p = ps[ps.length-1];
  state.graph.patch_history.push({...p, result:"applied", timestamp:new Date().toISOString()});
  state.graph.proposals = ps.slice(0,-1);
  state.lastAction = `Applied: "${p.reason}"`;
  renderAll();
}

async function resetDemo() {
  setBusy(true, "Resetting…");
  try {
    const r = await fetchJson("/api/reset", {method:"POST"});
    if (r.session||r.graph) {
      applySnapshot({session:r.session||state.session,graph:r.graph||state.graph,events:[]}, "api");
      state.connected=true; state.source="api"; state.lastAction="Reset from backend."; renderAll(); setBusy(false); return;
    }
  } catch(err) { console.error(err); }
  applySnapshot(fallbackSnapshot, "fallback");
  state.nodeLayouts = {};
  ensureNodeLayouts(state.graph);
  state.lastAction = "Reset to demo.";
  renderAll();
  setBusy(false);
}

// ── renderAll ─────────────────────────────────────
function renderAll() {
  renderHealth();
  renderSummaryStrip();
  renderChat();
  renderGraph();
  renderViewer();
  renderInspector();
  renderReflectorBanner();
}

// ── API loading ───────────────────────────────────
async function loadFromApi() {
  const [sR,gR,eR,tR,dR,cR,bR,pR] = await Promise.allSettled([
    fetchJson("/api/session"), fetchJson("/api/graph"), fetchJson("/api/events"),
    fetchJson("/api/tools"),   fetchJson("/api/domains"), fetchJson("/api/capabilities"),
    fetchJson("/api/tools/bridge/health"), fetchJson("/api/planner/health"),
  ]);
  if (sR.status==="fulfilled"||gR.status==="fulfilled"||eR.status==="fulfilled") {
    state.connected=true; state.source="api";
    const sp = sR.status==="fulfilled" ? sR.value : null;
    const gp = gR.status==="fulfilled" ? gR.value : null;
    applySnapshot({
      session: normalizeSessionPayload(sp)||normalizeSessionPayload(sp?.session)||sp,
      graph:   normalizeGraphPayload(gp)||normalizeGraphPayload(sp?.graph)||gp,
      events:  normalizeEventsPayload(eR.status==="fulfilled"?eR.value:[]),
    }, "api");
    state.toolCatalog      = tR.status==="fulfilled" ? tR.value?.tools||[]  : [];
    state.domainCatalog    = dR.status==="fulfilled" ? dR.value?.domains||{}: {};
    state.capabilitySummary= cR.status==="fulfilled" ? cR.value||{capabilities:[],tool_capabilities:{},domain_capabilities:{}} : {capabilities:[],tool_capabilities:{},domain_capabilities:{}};
    state.bridgeHealth     = bR.status==="fulfilled" ? bR.value||null : null;
    state.plannerHealth    = pR.status==="fulfilled" ? pR.value||null : null;
    state.session.tool_catalog = state.toolCatalog;
    state.session.capabilities = state.capabilitySummary?.capabilities||[];
    if (!state.graph.nodes.some(n=>n.node_id===state.selectedNodeId)) state.selectedNodeId=state.graph.nodes[0]?.node_id||null;
    state.lastAction="Loaded backend snapshot."; return;
  }
  throw new Error("Cannot reach backend.");
}

async function refreshFromApiOrFallback() {
  setBusy(true, "Connecting…");
  try { await loadFromApi(); }
  catch(err) { console.error(err); applySnapshot(fallbackSnapshot,"fallback"); state.lastAction="Demo mode."; }
  renderAll();
  setBusy(false);
}

// ── Chat action ───────────────────────────────────
async function sendChat(msg) {
  state.session.chat_history = [...(state.session.chat_history||[]), {role:"user",content:msg}];
  renderAll();
  setBusy(true, "Thinking…");
  try {
    const r = await fetchJson("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:msg})});
    if (r.session||r.graph) {
      applySnapshot({session:r.session||state.session,graph:r.graph||state.graph,events:r.graph?.events||state.events},"api");
    } else {
      const reply = r.reply?.content||r.reply||"Acknowledged.";
      state.session.chat_history = [...state.session.chat_history,{role:"assistant",content:reply}];
      if (Array.isArray(r.chat_history)) state.session.chat_history = r.chat_history;
    }
    state.connected=true; state.source="api";
  } catch(err) {
    console.error(err);
    state.session.chat_history = [...state.session.chat_history,{role:"assistant",content:"Backend unavailable — running in demo mode."}];
    state.connected=false; state.source="fallback";
  }
  renderAll(); setBusy(false);
}

async function createReviewProposal(reason) {
  setBusy(true,"Drafting proposal…");
  try {
    await fetchJson("/api/patch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reason})});
    await loadFromApi(); renderAll(); setBusy(false); return;
  } catch(err) { console.error(err); }
  const p = clone(fallbackSnapshot.graph.proposals[0]);
  p.reason = reason;
  state.graph.proposals = [...(state.graph.proposals||[]), p];
  renderAll(); setBusy(false);
}

// ── Tab switching ─────────────────────────────────
document.getElementById("inspector-tabs")?.addEventListener("click", e => {
  const btn = e.target.closest("[data-tab]");
  if (!btn) return;
  state.inspectorTab = btn.dataset.tab;
  document.querySelectorAll("#inspector-tabs .tab-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.tab === state.inspectorTab);
    b.setAttribute("aria-selected", String(b.dataset.tab === state.inspectorTab));
  });
  renderInspector();
});

// ── Chat controls ─────────────────────────────────
document.getElementById("chat-submit")?.addEventListener("click", async () => {
  const ta = document.getElementById("chat-input");
  const msg = ta?.value.trim();
  if (!msg) return;
  ta.value = "";
  await sendChat(msg);
});

document.getElementById("chat-input")?.addEventListener("keydown", async e => {
  if (e.key==="Enter" && (e.ctrlKey||e.metaKey)) {
    e.preventDefault();
    const msg = e.target.value.trim();
    if (!msg) return;
    e.target.value = "";
    await sendChat(msg);
  }
});

document.getElementById("send-patch")?.addEventListener("click", async () => {
  await createReviewProposal("Add a human review checkpoint before segmentation.");
});

// ── Graph controls ────────────────────────────────
document.getElementById("execute-next")?.addEventListener("click", async () => {
  setBusy(true,"Executing…");
  try {
    const r = await fetchJson("/api/execute/next",{method:"POST"});
    if (r.session||r.graph) {
      applySnapshot({session:r.session||state.session,graph:r.graph||state.graph,events:r.graph?.events||state.events},"api");
      state.connected=true; state.source="api"; state.lastAction=r.message||"Executed."; renderAll(); setBusy(false); return;
    }
  } catch(err) { console.error(err); }
  advanceExecution(); setBusy(false);
});

document.getElementById("execute-until-done")?.addEventListener("click", async () => {
  setBusy(true,"Running full pipeline…");
  try {
    const r = await fetchJson("/api/execute/until-done",{method:"POST"});
    if (r.session||r.graph) {
      applySnapshot({session:r.session||state.session,graph:r.graph||state.graph,events:r.graph?.events||state.events},"api");
      state.connected=true; state.source="api"; state.lastAction=`Run-to-end finished: ${r.graph?.status || "unknown"}.`; renderAll(); setBusy(false); return;
    }
  } catch(err) { console.error(err); }
  advanceExecutionUntilDone(); setBusy(false);
});

document.getElementById("apply-latest-proposal")?.addEventListener("click", async () => {
  setBusy(true,"Applying proposal…");
  try {
    const r = await fetchJson("/api/proposals/apply-latest",{method:"POST"});
    if (r.session||r.graph) {
      applySnapshot({session:r.session||state.session,graph:r.graph||state.graph,events:r.graph?.events||state.events},"api");
      state.connected=true; state.source="api"; renderAll(); setBusy(false); return;
    }
  } catch(err) { console.error(err); }
  applyLatestProposal(); setBusy(false);
});

document.getElementById("reset-demo")?.addEventListener("click", () => resetDemo());

// ── Boot ──────────────────────────────────────────
refreshFromApiOrFallback();


/* ══════════════════════════════════════
   PANEL RESIZE LOGIC
══════════════════════════════════════ */
(function() {
  const workspace = document.querySelector('.workspace');
  const centerCol = document.querySelector('.center-column');
  const graphPanel = document.querySelector('.graph-panel');

  // ── Column resize (left | center | right) ──────────
  function setupColResize(handleId, side) {
    const handle = document.getElementById(handleId);
    if (!handle || !workspace) return;

    handle.addEventListener('pointerdown', function(e) {
      e.preventDefault();
      handle.classList.add('dragging');
      document.body.classList.add('resizing');
      handle.setPointerCapture(e.pointerId);

      const startX     = e.clientX;
      const totalW     = workspace.getBoundingClientRect().width;
      // Read current pixel values
      const cs         = getComputedStyle(workspace);
      const cols       = cs.gridTemplateColumns.split(' ');
      // cols = [leftPx, 4px, centerPx, 4px, rightPx]
      const startLeft  = parseFloat(cols[0]);
      const startRight = parseFloat(cols[4]);

      function onMove(ev) {
        const dx = ev.clientX - startX;
        const HANDLE_W = 4 * 2; // two handles = 8px total
        const MIN = 200;

        if (side === 'left') {
          const newLeft   = Math.max(MIN, Math.min(startLeft + dx, totalW - startRight - HANDLE_W - MIN));
          workspace.style.setProperty('--col-left', newLeft + 'px');
        } else {
          const newRight  = Math.max(MIN, Math.min(startRight - dx, totalW - startLeft - HANDLE_W - MIN));
          workspace.style.setProperty('--col-right', newRight + 'px');
        }
      }

      function onUp() {
        handle.classList.remove('dragging');
        document.body.classList.remove('resizing');
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      }

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    });
  }

  // ── Row resize (graph | viewer) ───────────────────
  function setupRowResize(handleId) {
    const handle = document.getElementById(handleId);
    if (!handle || !centerCol || !graphPanel) return;

    handle.addEventListener('pointerdown', function(e) {
      e.preventDefault();
      handle.classList.add('dragging');
      document.body.classList.add('resizing-row');
      handle.setPointerCapture(e.pointerId);

      const startY      = e.clientY;
      const centerH     = centerCol.getBoundingClientRect().height;
      const startGraphH = graphPanel.getBoundingClientRect().height;

      function onMove(ev) {
        const dy       = ev.clientY - startY;
        const MIN      = 100;
        const HANDLE_H = 9;
        const newH     = Math.max(MIN, Math.min(startGraphH + dy, centerH - HANDLE_H - MIN));
        const pct      = (newH / centerH * 100).toFixed(2) + '%';
        graphPanel.style.height = pct;
        centerCol.style.setProperty('--row-graph', pct);
      }

      function onUp() {
        handle.classList.remove('dragging');
        document.body.classList.remove('resizing-row');
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      }

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
    });
  }

  // Init after DOM ready
  setupColResize('resize-col-left',  'left');
  setupColResize('resize-col-right', 'right');
  setupRowResize('resize-row-center');
})();



// ── Reflector banner ─────────────────────────────
function renderReflectorBanner() {
  const el = document.getElementById('reflector-banner');
  if (!el) return;
  const rs = state.reflectorState;
  if (!rs || !rs.active || state.reflectorDismissed) {
    el.classList.add('hidden');
    return;
  }
  el.classList.remove('hidden');
  const phaseLabel = {
    reflecting: '🧠 Brain LLM analyzing error…',
    retrying:   '↺ Retrying node from adjusted parameters…',
    resolved:   '✓ Error resolved — pipeline resumed',
  }[rs.phase] || rs.phase;
  const node = nodeById(rs.node_id);
  el.innerHTML = `
    <div class="reflector-spinner"></div>
    <div class="reflector-banner-text">
      <div class="reflector-banner-title">${escapeHtml(phaseLabel)}</div>
      <div class="reflector-banner-sub">Node: ${escapeHtml(rs.node_id)}${rs.attempt ? ' · Attempt #' + rs.attempt : ''} — ${escapeHtml(rs.reasoning || '')}</div>
    </div>
    <div class="reflector-banner-actions">
      <button class="banner-btn accept" id="reflector-retry-btn">Retry Now</button>
      <button class="banner-btn dismiss" id="reflector-dismiss-btn">Dismiss</button>
    </div>
  `;
  document.getElementById('reflector-retry-btn')?.addEventListener('click', async () => {
    await executeNextFromReflector();
  });
  document.getElementById('reflector-dismiss-btn')?.addEventListener('click', () => {
    state.reflectorDismissed = true;
    renderReflectorBanner();
  });
}

async function executeNextFromReflector() {
  const rs = state.reflectorState;
  if (!rs) return;
  setBusy(true, 'Retrying failed node…');
  try {
    const r = await fetchJson('/api/execute/next', { method: 'POST' });
    if (r.session || r.graph) {
      applySnapshot({ session: r.session||state.session, graph: r.graph||state.graph, events: r.graph?.events||state.events }, 'api');
      state.reflectorState = null;
      renderAll(); setBusy(false); return;
    }
  } catch(err) { console.error(err); }
  // Local simulation: move failed → retrying → succeeded
  const node = nodeById(rs.node_id);
  if (node) {
    node.status = 'retrying';
    state.reflectorState = { ...rs, phase: 'retrying' };
    renderAll();
    setTimeout(() => {
      const n2 = nodeById(rs.node_id);
      if (n2) {
        n2.status = 'succeeded';
        n2.retry_count = (n2.retry_count || 0) + 1;
        (n2.retry_history = n2.retry_history || []).push({ attempt: rs.attempt, ts: new Date().toISOString(), status: 'succeeded', error: null });
      }
      state.reflectorState = { ...rs, phase: 'resolved', active: false };
      state.reflectorDismissed = false;
      state.session.chat_history = [...(state.session.chat_history||[]),
        { role: 'reflector', content: '✓ Retry #' + rs.attempt + ' succeeded for node ' + rs.node_id + '.\n\nThe voxel resampling pre-step resolved the size mismatch. ADC registered to T2w space successfully. Pipeline resuming from segment_prostate.' }
      ];
      renderAll();
      setBusy(false);
    }, 1200);
  } else {
    setBusy(false);
  }
}
