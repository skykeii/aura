import json
from typing import List, Dict, Any, Tuple
from aura.drivers.base import Element
from aura.agent.llm import llm_client

SYSTEM_PROMPT = """You are AURA, an autonomous agentic black-box UI testing agent.
Your mission is to achieve the user's natural-language goal on the interface using ONLY the visual Set-of-Mark (SoM) numbered badges.
CRITICAL RULES:
1. You may NEVER use HTML selectors, XPaths, or IDs. You may ONLY reference element numbers (e.g. element_id: 3) or directional scrolling.
2. Carefully inspect the marked screenshot. Pick the exact mark number that brings you closer to the goal.
3. If an occluding popup or modal blocks your path, dismiss or close it first.
4. Output STRICT JSON with no markdown backticks and no additional text:
{
  "thought": "concise explanation of why this control was chosen",
  "action": "tap" | "type" | "scroll" | "key" | "done" | "stuck",
  "element_id": <int or null>,
  "args": {"text": "...", "dx": 0, "dy": 300, "name": "Tab"},
  "confidence": <float 0.0 to 1.0>,
  "goal_progress": "<percentage string like 50%>",
  "believes_done": <boolean>
}
"""

def build_user_prompt(
    goal: str,
    recent_steps: List[Dict[str, Any]],
    tried_at_state: List[int],
    step_n: int,
    current_url: str = "",
    page_title: str = ""
) -> str:
    history_summary = []
    for s in recent_steps[-6:]:
        action = s.get("action", {})
        history_summary.append(
            f"Step {s.get('n')}: Thought: {s.get('thought')[:70]} -> Action: {action.get('type')} on #{action.get('element_id')} (State Changed: {s.get('result', {}).get('state_changed')})"
        )
    
    hist_text = "\n".join(history_summary) if history_summary else "None (Initial Step)"
    tried_text = ", ".join(str(m) for m in tried_at_state) if tried_at_state else "None"

    url_line = f"Current Browser URL: {current_url}\n" if current_url else ""
    title_line = f"Current Page Title: {page_title}\n" if page_title else ""

    return f"""{url_line}{title_line}Target Goal: "{goal}"
Current Step: #{step_n}
Recent Actions:
{hist_text}

Already tried marks at this exact state: [{tried_text}] (CRITICAL: DO NOT repeat any of these marks as they produced no state change).

Analyze the interface elements and decide the optimal next action to advance or complete the goal."""

def format_elements_for_policy(elements: List[Element], tried_at_state: List[int] = None) -> str:
    tried_set = set(tried_at_state or [])
    lines = []
    # Up to 70 elements
    for e in elements[:70]:
        name_str = f'"{e.name}"' if e.name else "unlabelled"
        val_str = f' (value: "{e.value}")' if e.value else ""
        occ_str = f' [OCCLUDED by {e.occluded_by}]' if e.occluded_by else ""
        tried_str = " [ALREADY TRIED - DEAD CLICK]" if e.id in tried_set else ""
        lines.append(f"Mark #{e.id}: [{e.role}] {name_str}{val_str}{occ_str}{tried_str} bbox:[{e.bbox[0]},{e.bbox[1]},{e.bbox[2]},{e.bbox[3]}]")
    return "\n".join(lines)

def decide_next_action(
    goal: str,
    elements: List[Element],
    jpeg_bytes: bytes,
    recent_steps: List[Dict[str, Any]],
    tried_at_state: List[int],
    step_n: int,
    confidence_floor: float = 0.60,
    current_url: str = "",
    page_title: str = ""
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    elements_summary = format_elements_for_policy(elements, tried_at_state)
    user_prompt = build_user_prompt(goal, recent_steps, tried_at_state, step_n, current_url, page_title)
    
    decision, tokens = llm_client.query_vision_policy(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        jpeg_bytes=jpeg_bytes,
        elements_summary=elements_summary,
        goal=goal,
        recent_steps=recent_steps,
        current_url=current_url,
        page_title=page_title,
        tried_at_state=tried_at_state
    )

    # Sanitize and validate decision output
    confidence = float(decision.get("confidence", 0.75))
    action_type = decision.get("action", "tap")
    elem_id = decision.get("element_id")

    # If confidence is below floor and not exploring, convert to scroll exploration
    if confidence < confidence_floor and action_type not in ["scroll", "done", "stuck"]:
        decision["thought"] = f"[Low Confidence {confidence:.2f} < {confidence_floor}] Exploring viewport to reveal clearer visual cues."
        decision["action"] = "scroll"
        decision["element_id"] = None
        decision["args"] = {"dx": 0, "dy": 300}

    return decision, tokens
