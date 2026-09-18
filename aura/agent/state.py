from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Set

@dataclass
class Action:
    type: str                         # "tap" | "type" | "scroll" | "key" | "done" | "stuck"
    element_id: Optional[int] = None  # Set-of-Mark ID
    args: Dict[str, Any] = field(default_factory=dict) # e.g. {"text": "jacket"}, {"dx": 0, "dy": 300}, {"name": "Tab"}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class Finding:
    id: str
    run_id: str
    step_n: int
    category: str                     # "friction" | "a11y" | "persona" | "regression"
    rule_id: str                      # e.g. "touch_target_min", "axe:color-contrast"
    severity: str                     # "critical" | "major" | "minor"
    title: str
    evidence: Dict[str, Any]          # {"measured": ..., "threshold": ..., "unit": ...}
    element: Dict[str, Any]           # {"bbox": [...], "role": ..., "name": ..., "selector_hint": ...}
    screenshot: str                   # path or base64 URL
    cropped_png: str                  # path or base64 URL of evidence crop
    personas: List[str]               # ["protanopia", "dyslexia", "motor"]
    recommendation: str
    wishlisted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class Step:
    n: int
    ts: str
    goal_progress: str
    perception: Dict[str, Any]        # {"screenshot": ..., "overlay_png": ..., "element_count": ..., "state_sig": ...}
    thought: str
    action: Action
    confidence: float
    result: Dict[str, Any]            # {"state_changed": bool, "new_state_sig": ..., "latency_ms": ...}
    findings: List[str] = field(default_factory=list) # list of finding IDs

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

@dataclass
class RunState:
    run_id: str
    goal: str
    platform: str
    target_url: str
    status: str = "running"           # "running" | "paused" | "completed" | "stuck" | "stopped"
    current_step: int = 0
    steps: List[Step] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    state_history: List[str] = field(default_factory=list)
    loop_counts: Dict[str, int] = field(default_factory=dict)
    visited_transitions: Set[str] = field(default_factory=set)
    is_paused: bool = False
    interventions: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    start_time: float = 0.0

