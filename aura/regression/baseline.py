import sqlite3
import json
from datetime import datetime
from typing import Dict, Any, Optional
from aura.server.db import get_db_connection

def set_baseline(run_id: str, name: str = "v1_baseline", target_version: str = "shopdemo_v1") -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Check if run exists
    cur.execute("SELECT id FROM runs WHERE id = ?", (run_id,))
    if not cur.fetchone():
        conn.close()
        return False
        
    cur.execute("UPDATE runs SET is_baseline = 1 WHERE id = ?", (run_id,))
    cur.execute("""
        INSERT OR REPLACE INTO baselines (id, name, run_id, target_version, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (f"base_{name}", name, run_id, target_version, datetime.utcnow().isoformat()))
    
    conn.commit()
    conn.close()
    return True

def get_latest_baseline(name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()
    if name:
        cur.execute("SELECT * FROM baselines WHERE name = ? ORDER BY created_at DESC LIMIT 1", (name,))
    else:
        cur.execute("SELECT * FROM baselines ORDER BY created_at DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None
