import os
import uuid
import asyncio
from typing import Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel

from aura.server.db import (
    init_db, get_all_runs, get_run_details, toggle_wishlist_db,
    get_wishlist_items, DB_PATH
)
from aura.server.events import event_bus
from aura.agent.state import RunState
from aura.agent.loop import AgentLoop
from aura.drivers.web import WebDriver
from aura.drivers.android import AndroidDriver
from aura.drivers.ios import IOSDriver
from aura.agent.explorer import journey_graph_builder
from aura.remediation.batch import remediation_engine
from aura.remediation.exporters import create_remediation_zip
from aura.regression.baseline import set_baseline, get_latest_baseline
from aura.regression.differ import regression_differ
from aura.auditors.personas import simulate_color_blindness, personas_auditor
from aura.agent.llm import llm_client

app = FastAPI(title="AURA - Autonomous Agentic UI/UX & A11y Framework")

# Active run loops
active_loops: Dict[str, AgentLoop] = {}
cached_bundle: Dict[str, str] = {}

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
TARGET_V1_DIR = os.path.join(BASE_DIR, "aura", "targets", "shopdemo_v1")
TARGET_V2_DIR = os.path.join(BASE_DIR, "aura", "targets", "shopdemo_v2")

os.makedirs(RUNS_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount statics
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/runs", StaticFiles(directory=RUNS_DIR), name="runs")
app.mount("/targets/shopdemo_v1", StaticFiles(directory=TARGET_V1_DIR, html=True), name="shopdemo_v1")
app.mount("/targets/shopdemo_v2", StaticFiles(directory=TARGET_V2_DIR, html=True), name="shopdemo_v2")

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")

# ----------------- WebSocket Live Stream & Control -----------------
@app.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str):
    await websocket.accept()
    event_bus.register(run_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            loop = active_loops.get(run_id)

            if msg_type == "pause" and loop:
                loop.pause()
                await event_bus.emit(run_id, "status", {"status": "paused"})
            elif msg_type == "resume" and loop:
                loop.resume()
                await event_bus.emit(run_id, "status", {"status": "running"})
            elif msg_type == "stop" and loop:
                loop.request_stop()
                await event_bus.emit(run_id, "status", {"status": "stopped"})
            elif msg_type == "human_action" and loop:
                action = data.get("action", {})
                loop.inject_human_action(action)
    except WebSocketDisconnect:
        event_bus.unregister(run_id, websocket)
    except Exception:
        event_bus.unregister(run_id, websocket)

# ----------------- Settings Endpoints -----------------
@app.get("/api/settings")
def get_settings():
    return llm_client.get_settings()

@app.post("/api/settings")
def update_settings(payload: Dict[str, Any]):
    return llm_client.update_settings(payload)

@app.post("/api/settings/test")
def test_settings(payload: Dict[str, Any]):
    return llm_client.test_connection(
        provider=payload.get("provider", "heuristic"),
        api_key=payload.get("api_key", ""),
        model_name=payload.get("model_name", "")
    )

# ----------------- REST Endpoints -----------------
class RunRequest(BaseModel):
    goal: str
    platform: str = "desktop" # "desktop" | "mobile_web" | "android" | "ios"
    target_url: Optional[str] = None
    max_steps: int = 12
    provider: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None

@app.post("/api/run")
async def start_run(req: RunRequest):
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    
    # Handle platform selection
    if req.platform == "ios":
        raise HTTPException(status_code=400, detail="iOS driver is interface-only in this hackathon release.")
    
    # Configure LLM provider if explicitly provided
    if req.provider:
        llm_client.provider = req.provider.lower().strip()
    if req.api_key:
        if req.provider == "gemini":
            llm_client.gemini_key = req.api_key.strip()
        elif req.provider == "groq":
            llm_client.groq_key = req.api_key.strip()
        elif req.provider == "openai":
            llm_client.openai_key = req.api_key.strip()
    if req.model_name:
        llm_client.model_name = req.model_name.strip()

    target = (req.target_url or "").strip()
    if not target:
        target = "https://www.google.com"
    elif not target.startswith("http://") and not target.startswith("https://"):
        target = f"https://{target}"

    main_loop = asyncio.get_running_loop()

    def worker_job():
        if req.platform == "android":
            driver = AndroidDriver()
        else:
            driver = WebDriver(headless=True)

        profile = {"mode": req.platform}
        try:
            driver.start(target, profile)
            state = RunState(
                run_id=run_id,
                goal=req.goal,
                platform=req.platform,
                target_url=target
            )
            loop = AgentLoop(driver=driver, state=state, async_loop=main_loop)
            active_loops[run_id] = loop

            loop.run_sync(max_steps=req.max_steps)
            journey_graph_builder.add_episode(1, loop.state.steps)
        except Exception as e:
            print(f"[Worker Error in run {run_id}]: {e}")
        finally:
            try:
                driver.stop()
            except Exception:
                pass
            if run_id in active_loops:
                del active_loops[run_id]

    main_loop.run_in_executor(None, worker_job)

    return {"run_id": run_id, "status": "started", "goal": req.goal, "platform": req.platform}


@app.post("/api/pause/{run_id}")
async def pause_run(run_id: str):
    loop = active_loops.get(run_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Run loop not active")
    loop.pause()
    return {"status": "paused"}

@app.post("/api/resume/{run_id}")
async def resume_run(run_id: str):
    loop = active_loops.get(run_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Run loop not active")
    loop.resume()
    return {"status": "running"}

@app.post("/api/intervene/{run_id}")
async def intervene_run(run_id: str, action: Dict[str, Any]):
    loop = active_loops.get(run_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Run loop not active")
    loop.inject_human_action(action)
    return {"status": "intervention_registered"}

@app.post("/api/wishlist/{finding_id}")
async def toggle_wishlist(finding_id: str):
    new_state = toggle_wishlist_db(finding_id)
    return {"finding_id": finding_id, "wishlisted": new_state}

@app.get("/api/wishlist")
async def get_wishlist():
    items = get_wishlist_items()
    return {"items": items, "count": len(items)}

@app.post("/api/remediation/resolve-all")
async def resolve_all_wishlist():
    global cached_bundle
    items = get_wishlist_items()
    if not items:
        # Fallback to recent findings if wishlist is empty
        runs = get_all_runs()
        if runs:
            latest = get_run_details(runs[0]["id"])
            items = latest.get("findings", [])

    root_causes = remediation_engine.cluster_findings(items)
    bundle = remediation_engine.generate_bundle(root_causes)
    cached_bundle = bundle

    return {
        "root_causes": [rc.to_dict() for rc in root_causes],
        "bundle": bundle
    }

@app.get("/api/remediation/download")
async def download_bundle():
    global cached_bundle
    if not cached_bundle:
        items = get_wishlist_items()
        root_causes = remediation_engine.cluster_findings(items)
        cached_bundle = remediation_engine.generate_bundle(root_causes)

    zip_bytes = create_remediation_zip(cached_bundle)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=aura_fix_bundle.zip"}
    )

@app.get("/api/runs")
async def list_runs():
    return {"runs": get_all_runs()}

@app.get("/api/run/{run_id}")
async def fetch_run(run_id: str):
    details = get_run_details(run_id)
    if not details:
        raise HTTPException(status_code=404, detail="Run not found")
    return details

@app.post("/api/baseline")
async def mark_baseline(run_id: str = Query(...), name: str = "v1_baseline", target: str = "shopdemo_v1"):
    success = set_baseline(run_id, name, target)
    if not success:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "baseline_saved", "name": name, "run_id": run_id}

@app.get("/api/diff")
async def get_regression_diff(run_a: str, run_b: str):
    diff_data = regression_differ.diff_runs(run_a, run_b)
    return diff_data

@app.get("/api/graph/{run_id}")
async def get_graph(run_id: str):
    elements = journey_graph_builder.to_cytoscape_elements()
    comparison = journey_graph_builder.compute_path_comparison()
    return {"elements": elements, "comparison": comparison}

class PersonaRewriteRequest(BaseModel):
    finding: Dict[str, Any]
    persona: str

@app.post("/api/persona/rewrite")
async def rewrite_finding(req: PersonaRewriteRequest):
    from aura.agent.state import Finding
    f = Finding(**req.finding)
    narrative = personas_auditor.rewrite_for_persona(f, req.persona)
    return {"recommendation": narrative}
