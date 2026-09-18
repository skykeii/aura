import asyncio
import json
from datetime import datetime
from typing import Dict, Any, Set
from fastapi import WebSocket

class EventBus:
    """Thread-safe and async event bus for broadcasting run events to WebSockets."""
    def __init__(self):
        self._listeners: Dict[str, Set[WebSocket]] = {}
        self._seq_counters: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    def register(self, run_id: str, websocket: WebSocket):
        if run_id not in self._listeners:
            self._listeners[run_id] = set()
        self._listeners[run_id].add(websocket)

    def unregister(self, run_id: str, websocket: WebSocket):
        if run_id in self._listeners and websocket in self._listeners[run_id]:
            self._listeners[run_id].remove(websocket)
            if not self._listeners[run_id]:
                del self._listeners[run_id]

    async def emit(self, run_id: str, kind: str, payload: Dict[str, Any], tokens: Dict[str, int] = None, latency_ms: int = 0):
        if run_id not in self._seq_counters:
            self._seq_counters[run_id] = 0
        self._seq_counters[run_id] += 1
        seq = self._seq_counters[run_id]

        event = {
            "run_id": run_id,
            "seq": seq,
            "ts": datetime.utcnow().isoformat(),
            "kind": kind,
            "payload": payload,
            "tokens": tokens or {"in": 0, "out": 0},
            "latency_ms": latency_ms
        }

        # Send to all connected WebSockets for this run_id, plus global listeners ("*")
        targets = set()
        if run_id in self._listeners:
            targets.update(self._listeners[run_id])
        if "*" in self._listeners:
            targets.update(self._listeners["*"])

        dead_sockets = set()
        for ws in targets:
            try:
                await ws.send_text(json.dumps(event))
            except Exception:
                dead_sockets.add(ws)

        for ws in dead_sockets:
            for s in self._listeners.values():
                s.discard(ws)

        return event

event_bus = EventBus()
