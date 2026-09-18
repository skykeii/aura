import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "runs", "aura.db")

def get_db_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        platform TEXT NOT NULL,
        target_url TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        metrics TEXT,
        is_baseline INTEGER DEFAULT 0
    )
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step_n INTEGER NOT NULL,
        ts TEXT NOT NULL,
        goal_progress TEXT,
        thought TEXT,
        action_type TEXT,
        element_id INTEGER,
        action_args TEXT,
        confidence REAL,
        state_changed INTEGER,
        state_sig TEXT,
        latency_ms INTEGER,
        screenshot_path TEXT,
        overlay_path TEXT,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    )
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS findings (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        step_n INTEGER NOT NULL,
        category TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        evidence TEXT,
        element TEXT,
        screenshot_path TEXT,
        cropped_path TEXT,
        personas TEXT,
        recommendation TEXT,
        wishlisted INTEGER DEFAULT 0,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    )
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wishlist (
        id TEXT PRIMARY KEY,
        finding_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        notes TEXT,
        resolved INTEGER DEFAULT 0,
        FOREIGN KEY (finding_id) REFERENCES findings(id)
    )
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS baselines (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        run_id TEXT NOT NULL,
        target_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    )
    """)
    
    conn.commit()
    conn.close()

def save_run(run_id: str, goal: str, platform: str, target_url: str, status: str = "running"):
    conn = get_db_connection()
    conn.execute(
        "INSERT OR REPLACE INTO runs (id, goal, platform, target_url, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, goal, platform, target_url, status, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

def update_run_status(run_id: str, status: str, metrics: Optional[Dict[str, Any]] = None):
    conn = get_db_connection()
    metrics_json = json.dumps(metrics) if metrics else None
    conn.execute(
        "UPDATE runs SET status = ?, completed_at = ?, metrics = ? WHERE id = ?",
        (status, datetime.utcnow().isoformat(), metrics_json, run_id)
    )
    conn.commit()
    conn.close()

def save_step(run_id: str, step_data: Dict[str, Any]):
    conn = get_db_connection()
    action = step_data.get("action", {})
    conn.execute("""
        INSERT INTO steps (
            run_id, step_n, ts, goal_progress, thought, action_type,
            element_id, action_args, confidence, state_changed,
            state_sig, latency_ms, screenshot_path, overlay_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id,
        step_data.get("n", 0),
        step_data.get("ts", datetime.utcnow().isoformat()),
        step_data.get("goal_progress", ""),
        step_data.get("thought", ""),
        action.get("type", ""),
        action.get("element_id"),
        json.dumps(action.get("args", {})),
        step_data.get("confidence", 1.0),
        1 if step_data.get("result", {}).get("state_changed", False) else 0,
        step_data.get("result", {}).get("new_state_sig", ""),
        step_data.get("result", {}).get("latency_ms", 0),
        step_data.get("perception", {}).get("screenshot", ""),
        step_data.get("perception", {}).get("overlay_png", "")
    ))
    conn.commit()
    conn.close()

def save_finding(run_id: str, finding: Dict[str, Any]):
    conn = get_db_connection()
    conn.execute("""
        INSERT OR REPLACE INTO findings (
            id, run_id, step_n, category, rule_id, severity,
            title, evidence, element, screenshot_path,
            cropped_path, personas, recommendation, wishlisted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        finding.get("id"),
        run_id,
        finding.get("step_n", 0),
        finding.get("category", "friction"),
        finding.get("rule_id", ""),
        finding.get("severity", "minor"),
        finding.get("title", ""),
        json.dumps(finding.get("evidence", {})),
        json.dumps(finding.get("element", {})),
        finding.get("screenshot", ""),
        finding.get("cropped_png", ""),
        json.dumps(finding.get("personas", [])),
        finding.get("recommendation", ""),
        1 if finding.get("wishlisted", False) else 0
    ))
    conn.commit()
    conn.close()

def toggle_wishlist_db(finding_id: str) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT wishlisted, run_id FROM findings WHERE id = ?", (finding_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    
    new_status = 0 if row["wishlisted"] else 1
    cur.execute("UPDATE findings SET wishlisted = ? WHERE id = ?", (new_status, finding_id))
    
    if new_status == 1:
        cur.execute("INSERT OR REPLACE INTO wishlist (id, finding_id, run_id, created_at) VALUES (?, ?, ?, ?)",
                    (f"w_{finding_id}", finding_id, row["run_id"], datetime.utcnow().isoformat()))
    else:
        cur.execute("DELETE FROM wishlist WHERE finding_id = ?", (finding_id,))
        
    conn.commit()
    conn.close()
    return bool(new_status)

def get_wishlist_items() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT f.*, w.created_at as wishlisted_at, w.notes, r.platform, r.target_url
        FROM wishlist w
        JOIN findings f ON w.finding_id = f.id
        JOIN runs r ON f.run_id = r.id
        ORDER BY w.created_at DESC
    """)
    rows = cur.fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["evidence"] = json.loads(item["evidence"]) if item.get("evidence") else {}
        item["element"] = json.loads(item["element"]) if item.get("element") else {}
        item["personas"] = json.loads(item["personas"]) if item.get("personas") else []
        items.append(item)
    conn.close()
    return items

def get_all_runs() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM runs ORDER BY created_at DESC")
    rows = cur.fetchall()
    results = []
    for r in rows:
        item = dict(r)
        item["metrics"] = json.loads(item["metrics"]) if item.get("metrics") else {}
        results.append(item)
    conn.close()
    return results

def get_run_details(run_id: str) -> Dict[str, Any]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    run_row = cur.fetchone()
    if not run_row:
        conn.close()
        return {}
    
    run = dict(run_row)
    run["metrics"] = json.loads(run["metrics"]) if run.get("metrics") else {}
    
    cur.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY step_n ASC", (run_id,))
    run["steps"] = [dict(r) for r in cur.fetchall()]
    for s in run["steps"]:
        s["action_args"] = json.loads(s["action_args"]) if s.get("action_args") else {}
        
    cur.execute("SELECT * FROM findings WHERE run_id = ? ORDER BY step_n ASC", (run_id,))
    findings = []
    for r in cur.fetchall():
        f = dict(r)
        f["evidence"] = json.loads(f["evidence"]) if f.get("evidence") else {}
        f["element"] = json.loads(f["element"]) if f.get("element") else {}
        f["personas"] = json.loads(f["personas"]) if f.get("personas") else []
        findings.append(f)
    run["findings"] = findings
    
    conn.close()
    return run
