// AURA Autonomous Agentic UI/UX & Accessibility Platform Logic

let currentRunId = null;
let currentPlatform = "desktop";
let currentLens = "all";
let currentStationTab = "terminal";
let ws = null;
let isPaused = false;
let cy = null;
let trendChart = null;
let currentBundle = {};
let allFindings = [];
let allSteps = [];
let pastRuns = [];
let lastFrameTime = performance.now();
let fpsInterval = null;

document.addEventListener("DOMContentLoaded", () => {
  initPlatformSelectors();
  initJourneyGraph();
  initScorecardChart();
  fetchPastRuns();
  updateWishlistCount();
  setupLiveControlListeners();
  initAiSettings();
});

// ----------------- Platform Selector -----------------
function initPlatformSelectors() {
  document.querySelectorAll(".platform-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".platform-btn").forEach(b => {
        b.className = "platform-btn px-2.5 py-1 rounded-md text-zinc-400 hover:text-white text-[11px] transition";
      });
      btn.className = "platform-btn px-2.5 py-1 rounded-md bg-blue-600 text-white font-medium text-[11px] transition shadow";
      currentPlatform = btn.getAttribute("data-platform");

      const resTag = document.getElementById("viewport-resolution-tag");
      if (resTag) {
        resTag.innerText = currentPlatform === "mobile_web" ? "393 × 852 (Touch)" : (currentPlatform === "android" ? "1080 × 2400" : "1280 × 800");
      }
    });
  });
}

// ----------------- Station Tab Navigation -----------------
function switchStationTab(tab) {
  currentStationTab = tab;
  const tabs = ["terminal", "findings", "graph", "analytics"];
  
  tabs.forEach(t => {
    const btn = document.getElementById(`tab-station-${t}`);
    const content = document.getElementById(`station-content-${t}`);
    if (t === tab) {
      btn.className = "px-3 py-1.5 rounded-lg bg-blue-600 text-white font-bold text-xs shadow transition flex items-center space-x-1.5";
      content.classList.remove("hidden");
    } else {
      btn.className = "px-3 py-1.5 rounded-lg text-zinc-400 hover:text-white font-medium text-xs transition flex items-center space-x-1.5";
      content.classList.add("hidden");
    }
  });

  if (tab === "graph" && currentRunId) {
    refreshJourneyGraph(currentRunId);
  }
}

// ----------------- Run Execution -----------------
async function startNewRun() {
  const goal = document.getElementById("input-goal").value.trim();
  const target = document.getElementById("input-target").value.trim();

  if (!target) {
    alert("Please provide a target URL (e.g. https://www.youtube.com or https://www.google.com).");
    document.getElementById("input-target").focus();
    return;
  }
  if (!goal) {
    alert("Please describe what the agent should accomplish (e.g. Search for lofi hip hop and play the first video).");
    document.getElementById("input-goal").focus();
    return;
  }

  // Update UI to running state
  document.getElementById("btn-run-text").innerText = "Executing...";
  document.getElementById("btn-run").disabled = true;
  updateEngineStatus("RUNNING", "bg-blue-500 animate-ping", "text-blue-400");

  // Hide empty state placeholder
  const emptyState = document.getElementById("live-empty-state");
  if (emptyState) emptyState.classList.add("hidden");

  // Clear previous session data
  allFindings = [];
  allSteps = [];
  document.getElementById("findings-list").innerHTML = '<div class="text-xs text-zinc-500 italic p-6 text-center">Auditors listening for live findings...</div>';
  document.getElementById("step-trace-list").innerHTML = '';
  document.getElementById("terminal-log-stream").innerHTML = '';
  document.getElementById("rail-thought-text").innerText = "Initializing black-box driver session...";

  appendTerminalLog("SYSTEM", `Connecting black-box driver [${currentPlatform}] to ${target}...`);
  appendTerminalLog("GOAL", `Registered intent: "${goal}"`);

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        goal: goal,
        platform: currentPlatform,
        target_url: target,
        max_steps: 12,
        provider: currentAiSettings.provider
      })
    });

    const data = await res.json();
    currentRunId = data.run_id;
    appendTerminalLog("SYSTEM", `Session initialized successfully: ${currentRunId}`);
    connectWebSocket(currentRunId);
  } catch (err) {
    alert("Failed to start run: " + err);
    resetRunButton();
    updateEngineStatus("READY", "bg-emerald-500 animate-pulse", "text-emerald-400");
  }
}

function updateEngineStatus(text, dotClass, textClass) {
  const pill = document.getElementById("status-pill");
  const statusText = document.getElementById("status-text");
  if (statusText) {
    statusText.innerText = `ENGINE ${text}`;
    statusText.className = `${textClass} font-semibold`;
  }
  if (pill) {
    const dot = pill.querySelector("span:first-child");
    if (dot) dot.className = `w-2 h-2 rounded-full ${dotClass}`;
  }
}

function resetRunButton() {
  document.getElementById("btn-run-text").innerText = "Launch";
  document.getElementById("btn-run").disabled = false;
}

// ----------------- WebSocket Live Feed & Event Bus -----------------
function connectWebSocket(runId) {
  if (ws) {
    ws.close();
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${window.location.host}/ws/${runId}`);

  ws.onmessage = (event) => {
    try {
      const evt = JSON.parse(event.data);
      renderEvent(evt);
    } catch (e) {
      console.error("WS Parse error:", e);
    }
  };

  ws.onclose = () => {
    resetRunButton();
  };
}

function renderEvent(evt) {
  const { kind, payload, tokens, latency_ms } = evt;

  switch (kind) {
    case "frame":
      renderFrame(payload);
      break;

    case "step":
      renderReasoningRail(payload, tokens);
      break;

    case "finding":
      renderFinding(payload);
      break;

    case "status":
      renderStatus(payload);
      break;

    case "human_intervention":
      renderIntervention(payload);
      break;

    case "await_input":
      showStuckNotification(payload);
      break;
  }
}

// ----------------- Live Frame Rendering -----------------
function renderFrame(payload) {
  const img = document.getElementById("live-frame");
  img.src = payload.frame_b64;

  // Compute FPS
  const now = performance.now();
  const delta = now - lastFrameTime;
  lastFrameTime = now;
  if (delta > 0) {
    const fps = (1000 / delta).toFixed(1);
    const fpsEl = document.getElementById("live-fps");
    if (fpsEl) fpsEl.innerText = `${fps} fps`;
  }

  const stepTag = document.getElementById("active-step-tag");
  if (stepTag && payload.step_n) {
    stepTag.innerText = `Step ${payload.step_n}`;
  }
}

// ----------------- Reasoning Rail & Terminal Logging -----------------
function renderReasoningRail(payload, tokens) {
  const thought = payload.thought || "Perceiving interface state...";
  document.getElementById("rail-thought-text").innerText = thought;
  
  const actionText = `${payload.action?.type?.toUpperCase()} #${payload.action?.element_id || ''}`;
  document.getElementById("rail-action-text").innerText = actionText;

  const conf = Math.round((payload.confidence || 0) * 100);
  document.getElementById("rail-confidence-badge").innerText = `Confidence: ${conf}%`;

  const prog = payload.goal_progress || "0%";
  document.getElementById("rail-progress-text").innerText = prog;
  document.getElementById("rail-progress-bar").style.width = prog;

  // Terminal Log Lines
  const engineTag = payload.engine ? `[${payload.engine}] ` : "";
  appendTerminalLog("THINK", `${engineTag}[Step ${payload.step_n}] ${thought} (Confidence: ${conf}%)`);
  appendTerminalLog("ACTION", `Dispatched coordinate action: ${actionText} on Mark #${payload.action?.element_id || 'viewport'}`);

  allSteps.push(payload);
  renderStepTraceItem(payload, allSteps.length);

  // Animate action ripple
  if (payload.target_bbox && payload.target_bbox[2] > 0) {
    animateActionPoint(payload.target_bbox);
  }
}

function appendTerminalLog(tag, message) {
  const stream = document.getElementById("terminal-log-stream");
  if (!stream) return;

  const div = document.createElement("div");
  div.className = "flex items-start space-x-2";

  let tagColor = "text-zinc-500";
  if (tag === "THINK") tagColor = "text-blue-400 font-bold";
  else if (tag === "ACTION") tagColor = "text-emerald-400 font-bold";
  else if (tag === "AUDIT") tagColor = "text-amber-400 font-bold";
  else if (tag === "HUMAN") tagColor = "text-purple-400 font-bold";
  else if (tag === "VERIFY") tagColor = "text-cyan-400 font-bold";
  else if (tag === "SYSTEM") tagColor = "text-zinc-400";
  else if (tag === "GOAL") tagColor = "text-indigo-400 font-bold";

  div.innerHTML = `
    <span class="${tagColor}">[${tag}]</span>
    <span class="text-zinc-300 flex-1">${message}</span>
  `;

  stream.appendChild(div);
  stream.scrollTop = stream.scrollHeight;
}

function animateActionPoint(bbox) {
  const container = document.getElementById("live-container");
  const img = document.getElementById("live-frame");
  if (!img.naturalWidth) return;

  const rect = img.getBoundingClientRect();
  const scaleX = rect.width / img.naturalWidth;
  const scaleY = rect.height / img.naturalHeight;

  const cx = rect.left + (bbox[0] + bbox[2] / 2) * scaleX - container.getBoundingClientRect().left;
  const cy = rect.top + (bbox[1] + bbox[3] / 2) * scaleY - container.getBoundingClientRect().top;

  const ripple = document.createElement("div");
  ripple.className = "ripple";
  ripple.style.left = `${cx}px`;
  ripple.style.top = `${cy}px`;
  container.appendChild(ripple);
  setTimeout(() => ripple.remove(), 600);
}

// ----------------- Step Trace History -----------------
function renderStepTraceItem(step, stepN) {
  const list = document.getElementById("step-trace-list");
  if (stepN === 1) list.innerHTML = '';

  document.getElementById("step-count-badge").innerText = `${stepN} steps recorded`;

  const div = document.createElement("div");
  div.className = "flex items-center justify-between p-1.5 rounded-lg bg-zinc-900/80 border border-white/5 hover:border-blue-500/50 transition cursor-pointer text-[11px] font-mono";
  div.innerHTML = `
    <div class="flex items-center space-x-2">
      <span class="px-1.5 py-0.2 rounded bg-blue-950 text-blue-400 font-bold text-[10px]">#${stepN}</span>
      <span class="text-zinc-200 font-semibold">${step.action?.type}</span>
      <span class="text-zinc-500">#${step.action?.element_id || '-'}</span>
    </div>
    <div class="flex items-center space-x-2">
      <span class="text-zinc-400 text-[10px]">${step.goal_progress || ''}</span>
      <span class="w-1.5 h-1.5 rounded-full ${step.confidence > 0.7 ? 'bg-emerald-500' : 'bg-amber-500'}"></span>
    </div>
  `;
  list.appendChild(div);
  list.scrollTop = list.scrollHeight;
}

// ----------------- Pause & Take Control -----------------
function togglePauseControl() {
  if (!ws || !currentRunId) return;

  isPaused = !isPaused;
  const btn = document.getElementById("btn-pause-control");
  const banner = document.getElementById("control-overlay-banner");

  if (isPaused) {
    ws.send(JSON.stringify({ type: "pause" }));
    btn.innerHTML = `<span>▶</span><span>Resume Agent</span>`;
    btn.className = "px-3 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs shadow-md shadow-emerald-600/20 border border-emerald-400/30 transition flex items-center space-x-1";
    banner.classList.remove("hidden");
    updateEngineStatus("PAUSED / HUMAN CONTROL", "bg-amber-500", "text-amber-400");
    appendTerminalLog("HUMAN", "Agent paused. Manual coordinate control engaged on live viewport.");
  } else {
    ws.send(JSON.stringify({ type: "resume" }));
    btn.innerHTML = `<span>⏸</span><span>Pause & Take Control</span>`;
    btn.className = "px-3 py-1 rounded-lg bg-amber-600/90 hover:bg-amber-500 text-white font-bold text-xs shadow-md shadow-amber-600/20 border border-amber-400/30 transition flex items-center space-x-1";
    banner.classList.add("hidden");
    updateEngineStatus("RUNNING", "bg-blue-500 animate-ping", "text-blue-400");
    appendTerminalLog("HUMAN", "Resuming autonomous agent loop.");
  }
}

function setupLiveControlListeners() {
  const container = document.getElementById("live-container");
  const img = document.getElementById("live-frame");

  container.addEventListener("click", (e) => {
    if (!isPaused || !ws) return;

    const rect = img.getBoundingClientRect();
    const naturalW = img.naturalWidth || 1280;
    const naturalH = img.naturalHeight || 800;

    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    if (clickX < 0 || clickX > rect.width || clickY < 0 || clickY > rect.height) return;

    const targetX = Math.round((clickX / rect.width) * naturalW);
    const targetY = Math.round((clickY / rect.height) * naturalH);

    // Visual ripple
    const ripple = document.createElement("div");
    ripple.className = "ripple";
    ripple.style.left = `${e.clientX - container.getBoundingClientRect().left}px`;
    ripple.style.top = `${e.clientY - container.getBoundingClientRect().top}px`;
    container.appendChild(ripple);
    setTimeout(() => ripple.remove(), 600);

    appendTerminalLog("HUMAN", `Manual tap dispatched to coordinate (${targetX}, ${targetY})`);

    ws.send(JSON.stringify({
      type: "human_action",
      action: { type: "tap", x: targetX, y: targetY }
    }));
  });

  window.addEventListener("keydown", (e) => {
    if (!isPaused || !ws) return;
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

    if (e.key === "Enter" || e.key === "Tab" || e.key === "Escape") {
      appendTerminalLog("HUMAN", `Manual key sent: [${e.key}]`);
      ws.send(JSON.stringify({
        type: "human_action",
        action: { type: "key", name: e.key }
      }));
    } else if (e.key.length === 1) {
      appendTerminalLog("HUMAN", `Manual character typed: '${e.key}'`);
      ws.send(JSON.stringify({
        type: "human_action",
        action: { type: "type", text: e.key }
      }));
    }
  });
}

function renderIntervention(payload) {
  appendTerminalLog("HUMAN", `Human intervention executed on Step #${payload.step_n}`);
}

function showStuckNotification(payload) {
  appendTerminalLog("ALERT", `Agent paused: ${payload.reason}`);
}

// ----------------- Findings Feed & Lenses -----------------
function renderFinding(finding) {
  allFindings.push(finding);
  appendTerminalLog("AUDIT", `Flagged [${finding.rule_id}] (${finding.severity.toUpperCase()}): ${finding.title}`);
  updateFindingsUI();
}

function updateFindingsUI() {
  const list = document.getElementById("findings-list");
  const filtered = currentLens === "all" 
    ? allFindings 
    : allFindings.filter(f => (f.personas || []).includes(currentLens));

  document.getElementById("findings-count-badge").innerText = filtered.length;

  if (filtered.length === 0) {
    list.innerHTML = '<div class="text-xs text-zinc-500 italic p-6 text-center">No findings matching active lens.</div>';
    return;
  }

  list.innerHTML = '';
  filtered.forEach(f => {
    const sevColor = f.severity === 'critical' 
      ? 'border-rose-500/50 bg-rose-950/20 text-rose-300' 
      : f.severity === 'major' 
        ? 'border-amber-500/50 bg-amber-950/20 text-amber-300' 
        : 'border-blue-500/50 bg-blue-950/20 text-blue-300';

    const card = document.createElement("div");
    card.className = `p-3 rounded-xl border ${sevColor} space-y-2 transition`;
    card.innerHTML = `
      <div class="flex items-center justify-between">
        <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-black/60 border border-white/10 text-zinc-300">${f.rule_id}</span>
        <button onclick="toggleWishlist('${f.id}')" class="text-sm text-zinc-400 hover:text-amber-400 transition" title="Save to wishlist">
          ${f.wishlisted ? '⭐' : '☆'}
        </button>
      </div>
      <div class="font-bold text-xs text-zinc-100 leading-snug">${f.title}</div>
      <div class="text-[11px] text-zinc-400">
        <span class="text-zinc-500 font-mono">Evidence:</span> Measured: <span class="font-mono text-zinc-200">${f.evidence?.measured}</span> (Threshold: ${f.evidence?.threshold})
      </div>
      ${f.cropped_png ? `<img src="${f.cropped_png}" alt="Evidence crop" class="h-11 rounded-lg border border-white/10 mt-1 object-cover bg-black shadow" />` : ''}
      <div class="text-[11px] text-zinc-300 italic pt-1.5 border-t border-white/10">
        💡 ${f.recommendation}
      </div>
    `;
    list.appendChild(card);
  });
}

function setLens(lens) {
  currentLens = lens;
  document.querySelectorAll(".lens-btn").forEach(btn => {
    if (btn.getAttribute("data-lens") === lens) {
      btn.className = "lens-btn px-2.5 py-1 rounded-lg text-[11px] bg-blue-600 text-white font-medium";
    } else {
      btn.className = "lens-btn px-2.5 py-1 rounded-lg text-[11px] bg-zinc-800 text-zinc-400 hover:text-white";
    }
  });

  const img = document.getElementById("live-frame");
  if (lens === "deuteranopia") {
    img.style.filter = "sepia(0.2) saturate(1.4) hue-rotate(-20deg)";
  } else if (lens === "dyslexia") {
    img.style.filter = "contrast(1.2) brightness(0.95)";
  } else {
    img.style.filter = "none";
  }

  updateFindingsUI();
}

// ----------------- Journey Graph (Cytoscape) -----------------
function initJourneyGraph() {
  cy = cytoscape({
    container: document.getElementById('cy-journey'),
    style: [
      {
        selector: 'node',
        style: {
          'background-color': '#3b82f6',
          'label': 'data(label)',
          'color': '#f3f4f6',
          'font-size': '10px',
          'width': 26,
          'height': 26
        }
      },
      {
        selector: '.start-node',
        style: { 'background-color': '#10b981', 'width': 30, 'height': 30 }
      },
      {
        selector: '.goal-node',
        style: { 'background-color': '#059669', 'width': 32, 'height': 32 }
      },
      {
        selector: '.dead-end-node',
        style: { 'background-color': '#ef4444', 'width': 28, 'height': 28 }
      },
      {
        selector: 'edge',
        style: {
          'width': 2,
          'line-color': '#3b82f6',
          'target-arrow-color': '#3b82f6',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          'label': 'data(label)',
          'color': '#9ca3af',
          'font-size': '9px'
        }
      },
      {
        selector: '.alternate-edge',
        style: { 'line-color': '#a855f7', 'target-arrow-color': '#a855f7' }
      },
      {
        selector: '.loop-edge',
        style: { 'line-color': '#ef4444', 'target-arrow-color': '#ef4444' }
      }
    ],
    layout: { name: 'grid' }
  });
}

async function refreshJourneyGraph(runId) {
  if (!cy || !runId) return;
  try {
    const res = await fetch(`/api/graph/${runId}`);
    const data = await res.json();
    cy.elements().remove();
    cy.add(data.elements || []);
    cy.layout({ name: 'breadthfirst', directed: true, padding: 25 }).run();

    if (data.comparison) {
      document.getElementById("graph-metrics-tag").innerText = 
        `Optimal: ${data.comparison.optimal_steps} | First Path: ${data.comparison.first_path_steps} (Ratio: ${data.comparison.friction_ratio})`;
    }
  } catch (e) {}
}

// ----------------- Scorecards & Analytics -----------------
function renderStatus(payload) {
  if (payload.metrics) {
    const m = payload.metrics;
    document.getElementById("score-friction").innerText = `${m.friction_score}/100`;
    document.getElementById("score-a11y").innerText = `${m.a11y_score}/100`;
    document.getElementById("score-time").innerText = `${m.duration_sec}s`;
    document.getElementById("score-cost").innerText = `$${(m.cost_usd || 0).toFixed(4)}`;
  }

  if (payload.status === "completed" || payload.status === "stopped") {
    resetRunButton();
    updateEngineStatus("COMPLETED", "bg-emerald-500", "text-emerald-400");
    appendTerminalLog("SYSTEM", `Session concluded. Total steps: ${allSteps.length}, Findings: ${allFindings.length}`);
    refreshJourneyGraph(currentRunId);
    fetchPastRuns();
  }
}

function initScorecardChart() {
  const ctx = document.getElementById('scorecardChart').getContext('2d');
  trendChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: ['Run 1', 'Run 2', 'Run 3', 'Run 4', 'Latest'],
      datasets: [
        {
          label: 'Friction Score',
          data: [68, 75, 74, 82, 88],
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59, 130, 246, 0.1)',
          borderWidth: 2,
          tension: 0.3,
          fill: true
        },
        {
          label: 'WCAG Compliance',
          data: [55, 62, 70, 78, 85],
          borderColor: '#10b981',
          backgroundColor: 'rgba(16, 185, 129, 0.1)',
          borderWidth: 2,
          tension: 0.3,
          fill: true
        }
      ]
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#9ca3af', font: { family: 'Inter', size: 11 } } } },
      scales: {
        x: { ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
        y: { min: 0, max: 100, ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' } }
      }
    }
  });
}

// ----------------- Wishlist & Batch Fix -----------------
function toggleWishlistDrawer() {
  const drawer = document.getElementById("wishlist-drawer");
  const isHidden = drawer.classList.contains("translate-x-full");
  if (isHidden) {
    drawer.classList.remove("translate-x-full");
    loadWishlistItems();
  } else {
    drawer.classList.add("translate-x-full");
  }
}

async function toggleWishlist(findingId) {
  try {
    const res = await fetch(`/api/wishlist/${findingId}`, { method: "POST" });
    const data = await res.json();
    
    const target = allFindings.find(f => f.id === findingId);
    if (target) target.wishlisted = data.wishlisted;

    updateFindingsUI();
    updateWishlistCount();
  } catch (e) {}
}

async function updateWishlistCount() {
  try {
    const res = await fetch("/api/wishlist");
    const data = await res.json();
    const count = data.count || 0;
    document.getElementById("wishlist-badge").innerText = count;
    document.getElementById("drawer-count-badge").innerText = `${count} items`;
  } catch (e) {}
}

async function loadWishlistItems() {
  const container = document.getElementById("wishlist-drawer-content");
  try {
    const res = await fetch("/api/wishlist");
    const data = await res.json();
    const items = data.items || [];

    if (items.length === 0) {
      container.innerHTML = '<div class="text-xs text-zinc-500 italic text-center p-6">No bookmarked findings yet.</div>';
      return;
    }

    container.innerHTML = '';
    items.forEach(item => {
      const div = document.createElement("div");
      div.className = "p-3 rounded-xl bg-black/50 border border-white/10 text-xs space-y-1.5";
      div.innerHTML = `
        <div class="flex items-center justify-between">
          <span class="font-mono text-[10px] text-amber-400 font-bold">${item.rule_id}</span>
          <span class="text-[10px] text-zinc-500 font-mono">${item.platform || 'web'}</span>
        </div>
        <div class="font-bold text-zinc-200">${item.title}</div>
        <div class="text-[11px] text-zinc-400">${item.recommendation}</div>
      `;
      container.appendChild(div);
    });
  } catch (e) {}
}

async function openBatchFixModal() {
  document.getElementById("batch-fix-modal").classList.remove("hidden");
  try {
    const res = await fetch("/api/remediation/resolve-all", { method: "POST" });
    const data = await res.json();
    currentBundle = data.bundle || {};
    renderRootCauses(data.root_causes || []);
    switchBundleTab('css');
  } catch (e) {
    alert("Batch fix failed: " + e);
  }
}

function closeBatchFixModal() {
  document.getElementById("batch-fix-modal").classList.add("hidden");
}

function renderRootCauses(causes) {
  const container = document.getElementById("root-causes-container");
  container.innerHTML = '';
  causes.forEach(rc => {
    const div = document.createElement("div");
    div.className = "flex items-center justify-between p-3 rounded-xl bg-black/50 border border-white/10 text-xs";
    div.innerHTML = `
      <div class="flex items-center space-x-3">
        <input type="checkbox" checked class="accent-indigo-500 rounded" />
        <span class="font-mono text-indigo-400 font-bold">${rc.id}</span>
        <span class="text-zinc-200 font-medium">${rc.title}</span>
      </div>
      <div class="flex items-center space-x-2 font-mono">
        <span class="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-300">${rc.effort}</span>
        <span class="text-[10px] px-2 py-0.5 rounded bg-indigo-950 text-indigo-300 font-bold">${rc.count} instances</span>
      </div>
    `;
    container.appendChild(div);
  });
}

function switchBundleTab(tab) {
  document.querySelectorAll("[id^='tab-btn-']").forEach(b => {
    b.className = "px-3 py-1 rounded bg-zinc-800 text-zinc-400 text-xs font-medium";
  });
  document.getElementById(`tab-btn-${tab}`).className = "px-3 py-1 rounded bg-blue-600 text-white text-xs font-medium";

  const box = document.getElementById("bundle-preview-box");
  if (tab === 'css') {
    box.innerText = currentBundle.fixes_css || "/* fixes.css */";
  } else if (tab === 'aria') {
    box.innerText = currentBundle.aria_patches_md || "# aria-patches.md";
  } else {
    box.innerText = currentBundle.remediation_md || "# remediation.md";
  }
}

// ----------------- Regression Diff Modal -----------------
function toggleRegressionModal() {
  const modal = document.getElementById("regression-modal");
  modal.classList.toggle("hidden");
  if (!modal.classList.contains("hidden")) {
    populateDiffSelectors();
  }
}

function populateDiffSelectors() {
  const selA = document.getElementById("diff-select-a");
  const selB = document.getElementById("diff-select-b");
  selA.innerHTML = '';
  selB.innerHTML = '';

  pastRuns.forEach((r, idx) => {
    const optA = document.createElement("option");
    optA.value = r.id;
    optA.innerText = `${r.id} (${r.target_url.substring(0, 30)}...) - ${r.platform}`;
    
    const optB = optA.cloneNode(true);
    selA.appendChild(optA);
    selB.appendChild(optB);
  });

  if (selB.options.length > 1) {
    selB.selectedIndex = 1;
  }
}

async function calculateRegressionDiff() {
  const runA = document.getElementById("diff-select-a").value;
  const runB = document.getElementById("diff-select-b").value;

  try {
    const res = await fetch(`/api/diff?run_a=${runA}&run_b=${runB}`);
    const data = await res.json();

    document.getElementById("diff-fixed-count").innerText = data.issue_breakdown?.fixed_count || 0;
    document.getElementById("diff-still-count").innerText = data.issue_breakdown?.still_present_count || 0;
    document.getElementById("diff-new-count").innerText = data.issue_breakdown?.newly_introduced_count || 0;

    const steps = data.matched_steps || [];
    if (steps.length > 0) {
      document.getElementById("diff-img-before").src = steps[0].screenshot_a || "";
      document.getElementById("diff-img-after").src = steps[0].screenshot_b || "";
    }

    const tbody = document.getElementById("diff-table-body");
    tbody.innerHTML = '';

    (data.issue_breakdown?.fixed || []).forEach(f => {
      tbody.appendChild(createDiffRow("Fixed", "text-emerald-400 bg-emerald-950/40 border border-emerald-500/30", f));
    });
    (data.issue_breakdown?.still_present || []).forEach(f => {
      tbody.appendChild(createDiffRow("Still Present", "text-amber-400 bg-amber-950/40 border border-amber-500/30", f));
    });
    (data.issue_breakdown?.newly_introduced || []).forEach(f => {
      tbody.appendChild(createDiffRow("New", "text-rose-400 bg-rose-950/40 border border-rose-500/30", f));
    });
  } catch (e) {
    alert("Regression calculation failed: " + e);
  }
}

function createDiffRow(statusText, statusClass, f) {
  const tr = document.createElement("tr");
  tr.className = "border-b border-white/5 text-xs font-mono";
  tr.innerHTML = `
    <td class="py-2.5"><span class="px-2 py-0.5 rounded text-[10px] font-bold ${statusClass}">${statusText}</span></td>
    <td class="py-2.5 text-zinc-400">${f.rule_id}</td>
    <td class="py-2.5 text-zinc-200 font-sans">${f.title}</td>
    <td class="py-2.5 capitalize text-zinc-400">${f.severity}</td>
  `;
  return tr;
}

function updateDiffSlider(val) {
  document.getElementById("diff-slider-box").style.width = `${val}%`;
}

async function fetchPastRuns() {
  try {
    const res = await fetch("/api/runs");
    const data = await res.json();
    pastRuns = data.runs || [];
  } catch (e) {}
}

// ----------------- AI Engine & Settings -----------------
let currentAiSettings = {
  provider: "heuristic",
  active_provider: "heuristic",
  has_gemini_key: false,
  has_groq_key: false,
  has_openai_key: false,
  model_name: "",
  openai_base_url: "",
  active_engine_label: "Neural Heuristic (Zero-Key)"
};

async function initAiSettings() {
  try {
    const res = await fetch("/api/settings");
    if (res.ok) {
      currentAiSettings = await res.json();
      updateEngineHeaderBadge();
    }
  } catch (err) {
    console.error("Failed to load AI settings:", err);
  }
}

function updateEngineHeaderBadge() {
  const badge = document.getElementById("header-engine-badge");
  if (!badge) return;
  const prov = currentAiSettings.active_provider || "heuristic";
  if (prov === "gemini") {
    badge.innerText = "Gemini 2.5";
    badge.className = "px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/30 font-mono text-[10px]";
  } else if (prov === "groq") {
    badge.innerText = "Groq Llama";
    badge.className = "px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 font-mono text-[10px]";
  } else if (prov === "openai") {
    badge.innerText = "GPT-4o";
    badge.className = "px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-mono text-[10px]";
  } else {
    badge.innerText = "Zero-Key Smart";
    badge.className = "px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-mono text-[10px]";
  }
}

function openAiSettingsModal() {
  const modal = document.getElementById("ai-settings-modal");
  if (!modal) return;
  modal.classList.remove("hidden");

  // Populate fields
  const prov = currentAiSettings.provider || "heuristic";
  const radio = document.querySelector(`input[name="ai-provider"][value="${prov}"]`);
  if (radio) radio.checked = true;

  onProviderChanged(prov);

  const modelInput = document.getElementById("input-model-name");
  if (modelInput) modelInput.value = currentAiSettings.model_name || "";

  const baseUrlInput = document.getElementById("input-base-url");
  if (baseUrlInput) baseUrlInput.value = currentAiSettings.openai_base_url || "https://api.openai.com/v1";

  const resultBox = document.getElementById("ai-test-result");
  if (resultBox) resultBox.classList.add("hidden");
}

function closeAiSettingsModal() {
  const modal = document.getElementById("ai-settings-modal");
  if (modal) modal.classList.add("hidden");
}

function onProviderChanged(provider) {
  const keySection = document.getElementById("ai-key-section");
  const advancedSection = document.getElementById("ai-advanced-section");
  const keyInput = document.getElementById("input-api-key");
  const keyLabel = document.getElementById("ai-key-label");
  const keyHint = document.getElementById("ai-key-hint");

  if (provider === "heuristic") {
    keySection.classList.add("hidden");
    advancedSection.classList.add("hidden");
  } else {
    keySection.classList.remove("hidden");
    advancedSection.classList.remove("hidden");

    if (provider === "gemini") {
      keyLabel.innerText = "Google Gemini API Key";
      keyInput.placeholder = currentAiSettings.has_gemini_key ? "(Key is saved: " + currentAiSettings.gemini_key_masked + ")" : "AIzaSy...";
      keyHint.innerText = "Get a free Gemini API key from Google AI Studio (aistudio.google.com).";
    } else if (provider === "groq") {
      keyLabel.innerText = "Groq Cloud API Key";
      keyInput.placeholder = currentAiSettings.has_groq_key ? "(Key is saved: " + currentAiSettings.groq_key_masked + ")" : "gsk_...";
      keyHint.innerText = "Get an ultra-fast free API key from Groq Console (console.groq.com).";
    } else if (provider === "openai") {
      keyLabel.innerText = "OpenAI / OpenRouter API Key";
      keyInput.placeholder = currentAiSettings.has_openai_key ? "(Key is saved: " + currentAiSettings.openai_key_masked + ")" : "sk-...";
      keyHint.innerText = "Supports OpenAI, OpenRouter, and local OpenAI-compatible endpoints.";
    }
  }
}

function toggleApiKeyVisibility() {
  const input = document.getElementById("input-api-key");
  if (input) {
    input.type = input.type === "password" ? "text" : "password";
  }
}

async function testAiConnection() {
  const provider = document.querySelector('input[name="ai-provider"]:checked')?.value || "heuristic";
  const apiKey = document.getElementById("input-api-key").value.trim();
  const modelName = document.getElementById("input-model-name").value.trim();
  const resultBox = document.getElementById("ai-test-result");

  resultBox.classList.remove("hidden");
  resultBox.className = "p-2.5 rounded-xl text-xs font-mono bg-blue-950/60 border border-blue-600/40 text-blue-300";
  resultBox.innerText = "Testing connection to " + provider + "...";

  try {
    const res = await fetch("/api/settings/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: provider,
        api_key: apiKey,
        model_name: modelName
      })
    });
    const data = await res.json();
    if (data.status === "ok") {
      resultBox.className = "p-2.5 rounded-xl text-xs font-mono bg-emerald-950/60 border border-emerald-600/40 text-emerald-300";
      resultBox.innerText = "✓ " + data.message;
    } else {
      resultBox.className = "p-2.5 rounded-xl text-xs font-mono bg-rose-950/60 border border-rose-600/40 text-rose-300";
      resultBox.innerText = "✕ Error: " + data.message;
    }
  } catch (err) {
    resultBox.className = "p-2.5 rounded-xl text-xs font-mono bg-rose-950/60 border border-rose-600/40 text-rose-300";
    resultBox.innerText = "✕ Connection failed: " + err;
  }
}

async function saveAiSettings() {
  const provider = document.querySelector('input[name="ai-provider"]:checked')?.value || "heuristic";
  const apiKey = document.getElementById("input-api-key").value.trim();
  const modelName = document.getElementById("input-model-name").value.trim();
  const baseUrl = document.getElementById("input-base-url").value.trim();

  const payload = {
    provider: provider,
    model_name: modelName,
    openai_base_url: baseUrl
  };

  if (apiKey) {
    if (provider === "gemini") payload.gemini_key = apiKey;
    else if (provider === "groq") payload.groq_key = apiKey;
    else if (provider === "openai") payload.openai_key = apiKey;
  }

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      currentAiSettings = await res.json();
      updateEngineHeaderBadge();
      closeAiSettingsModal();
      appendTerminalLog("SYSTEM", `AI Engine updated: ${currentAiSettings.active_engine_label || currentAiSettings.active_provider}`);
    } else {
      alert("Failed to save settings: " + (await res.text()));
    }
  } catch (err) {
    alert("Error saving settings: " + err);
  }
}

