import asyncio
import time
import base64
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List
from aura.drivers.base import Driver, Element
from aura.agent.state import RunState, Step, Finding, Action
from aura.agent.perception import draw_som_overlay, save_frame_artifact
from aura.agent.policy import decide_next_action
from aura.agent.llm import llm_client
from aura.auditors.a11y import a11y_auditor
from aura.auditors.friction import friction_auditor
from aura.auditors.personas import personas_auditor
from aura.server.events import event_bus
from aura.server.db import save_run, update_run_status, save_step, save_finding

class AgentLoop:
    """Core autonomous agent execution loop: perceive → think → act → verify.
    Executes in a dedicated worker thread with Playwright sync API and bridges
    events thread-safely to the main asyncio event bus.
    """

    def __init__(self, driver: Driver, state: RunState, async_loop: Optional[asyncio.AbstractEventLoop] = None):
        self.driver = driver
        self.state = state
        self.async_loop = async_loop
        self._stop_requested = False
        self._is_paused = False
        self._pause_cond = threading.Condition()
        self._pending_interventions: List[Dict[str, Any]] = []

    def request_stop(self):
        self._stop_requested = True
        self.state.status = "stopped"
        with self._pause_cond:
            self._pause_cond.notify_all()

    def pause(self):
        with self._pause_cond:
            self._is_paused = True
            self.state.is_paused = True
            self.state.status = "paused"

    def resume(self):
        with self._pause_cond:
            self._is_paused = False
            self.state.is_paused = False
            self.state.status = "running"
            self._pause_cond.notify_all()

    def inject_human_action(self, action_data: Dict[str, Any]):
        """Accepts manual intervention (e.g. click-to-tap or type) during Pause/Take Control."""
        with self._pause_cond:
            self._pending_interventions.append(action_data)
            self.state.interventions += 1
            self._pause_cond.notify_all()

    def emit_event_sync(self, kind: str, payload: Dict[str, Any], tokens: Dict[str, int] = None, latency_ms: int = 0):
        """Thread-safe emission to the main asyncio WebSocket event bus."""
        if not self.async_loop or self.async_loop.is_closed():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                event_bus.emit(self.state.run_id, kind, payload, tokens, latency_ms),
                self.async_loop
            )
            # Do not block indefinitely
            future.result(timeout=2.0)
        except Exception as e:
            pass

    def run_sync(self, max_steps: int = 15) -> RunState:
        run_id = self.state.run_id
        save_run(run_id, self.state.goal, self.state.platform, self.state.target_url, "running")
        self.state.start_time = time.time()

        self.emit_event_sync("status", {
            "status": "running",
            "goal": self.state.goal,
            "platform": self.state.platform,
            "target_url": self.state.target_url
        })

        consecutive_dead_clicks = 0
        consecutive_low_confidence = 0
        state_visited_counts: Dict[str, int] = {}
        tried_actions_per_state: Dict[str, List[int]] = {}

        for step_idx in range(1, max_steps + 1):
            if self._stop_requested:
                break

            # Handle Pause & Human Intervention
            with self._pause_cond:
                while self._is_paused and not self._stop_requested:
                    if self._pending_interventions:
                        h_action = self._pending_interventions.pop(0)
                        self._execute_human_action(h_action, step_idx)
                    self._pause_cond.wait(timeout=0.5)

            if self._stop_requested:
                break

            step_start = time.time()
            self.state.current_step = step_idx

            # ----------------- 1. PERCEIVE -----------------
            raw_screenshot = self.driver.screenshot()
            elements = self.driver.elements()
            state_sig = self.driver.state_signature()

            # Track loops and repetitions
            loop_count = state_visited_counts.get(state_sig, 0)
            state_visited_counts[state_sig] = loop_count + 1
            if state_sig not in tried_actions_per_state:
                tried_actions_per_state[state_sig] = []

            # Draw Set-of-Mark Overlay
            jpeg_overlay, png_overlay, scaled_boxes = draw_som_overlay(raw_screenshot, elements)
            b64_frame = base64.b64encode(jpeg_overlay).decode("utf-8")

            # Stream live frame to WebSocket
            self.emit_event_sync("frame", {
                "frame_b64": f"data:image/jpeg;base64,{b64_frame}",
                "step_n": step_idx,
                "element_count": len(elements),
                "state_sig": state_sig
            })

            # ----------------- 2. THINK -----------------
            recent_steps_dicts = [s.to_dict() for s in self.state.steps]
            current_url = self.driver.current_url()
            page_title = self.driver.page_title()

            decision, tokens = decide_next_action(
                goal=self.state.goal,
                elements=elements,
                jpeg_bytes=jpeg_overlay,
                recent_steps=recent_steps_dicts,
                tried_at_state=tried_actions_per_state[state_sig],
                step_n=step_idx,
                current_url=current_url,
                page_title=page_title
            )

            thought = decision.get("thought", "")
            act_type = decision.get("action", "tap")
            chosen_elem_id = decision.get("element_id")
            args = decision.get("args", {})
            confidence = float(decision.get("confidence", 0.8))
            goal_progress = decision.get("goal_progress", f"{int((step_idx/max_steps)*100)}%")
            believes_done = decision.get("believes_done", False)

            # Track low confidence
            if confidence < 0.60:
                consecutive_low_confidence += 1
            else:
                consecutive_low_confidence = 0

            # Find matching target element
            target_element: Optional[Element] = None
            if chosen_elem_id:
                for e in elements:
                    if e.id == chosen_elem_id:
                        target_element = e
                        break
                if chosen_elem_id not in tried_actions_per_state[state_sig]:
                    tried_actions_per_state[state_sig].append(chosen_elem_id)

            # Highlight active target box and stream reasoning rail event
            self.emit_event_sync("step", {
                "step_n": step_idx,
                "thought": thought,
                "action": {"type": act_type, "element_id": chosen_elem_id, "args": args},
                "confidence": confidence,
                "goal_progress": goal_progress,
                "target_bbox": target_element.bbox if target_element else None,
                "engine": llm_client.active_engine_label,
                "current_url": current_url,
                "page_title": page_title
            }, tokens=tokens)

            # ----------------- 3. ACT -----------------
            pre_screenshot = raw_screenshot
            act_start = time.time()

            if act_type == "tap" and target_element:
                cx, cy = target_element.center
                self.driver.tap(cx, cy)
                # If text is provided for the tapped element (e.g. search bar or text input)
                txt = args.get("text")
                if txt:
                    time.sleep(0.3)
                    self.driver.type(txt)
                    if args.get("press_enter", True):
                        self.driver.key("Enter")
                        time.sleep(1.5)
            elif act_type == "type":
                if target_element:
                    cx, cy = target_element.center
                    self.driver.tap(cx, cy)
                    time.sleep(0.3)
                txt = args.get("text", "")
                self.driver.type(txt)
                if args.get("press_enter", True):
                    self.driver.key("Enter")
                    time.sleep(1.5)
            elif act_type == "scroll":
                dx = args.get("dx", 0)
                dy = args.get("dy", 300)
                self.driver.scroll(dx, dy)
            elif act_type == "key":
                k_name = args.get("name", "Enter")
                self.driver.key(k_name)

            post_screenshot = self.driver.screenshot()
            new_state_sig = self.driver.state_signature()
            latency_ms = int((time.time() - act_start) * 1000)

            # ----------------- 4. VERIFY -----------------
            state_changed = (new_state_sig != state_sig)
            if not state_changed and act_type == "tap":
                consecutive_dead_clicks += 1
                if chosen_elem_id and chosen_elem_id not in tried_actions_per_state[state_sig]:
                    tried_actions_per_state[state_sig].append(chosen_elem_id)
            else:
                consecutive_dead_clicks = 0

            # ----------------- 5. AUDIT -----------------
            step_findings: List[Finding] = []
            
            # Run Friction Auditor
            page_obj = getattr(self.driver, "page", None)
            f_findings = friction_auditor.audit_step(
                page=page_obj,
                elements=elements,
                pre_screenshot=pre_screenshot,
                post_screenshot=post_screenshot,
                target_element=target_element,
                action_type=act_type,
                latency_ms=latency_ms,
                state_changed=state_changed,
                consecutive_dead_clicks=consecutive_dead_clicks,
                loop_count=loop_count,
                run_id=run_id,
                step_n=step_idx
            )
            step_findings.extend(f_findings)

            # Run A11y Auditor
            a_findings = a11y_auditor.audit_page(
                page=page_obj,
                elements=elements,
                raw_screenshot=post_screenshot,
                run_id=run_id,
                step_n=step_idx
            )
            step_findings.extend(a_findings)

            # Run Personas Auditor
            p_findings = personas_auditor.audit_page(
                page=page_obj,
                raw_screenshot=post_screenshot,
                run_id=run_id,
                step_n=step_idx
            )
            step_findings.extend(p_findings)

            # Save and emit all findings
            finding_ids = []
            for f in step_findings:
                finding_ids.append(f.id)
                self.state.findings.append(f)
                save_finding(run_id, f.to_dict())
                self.emit_event_sync("finding", f.to_dict())

            # Save step artifacts
            screen_path = save_frame_artifact(run_id, step_idx, "screenshot", post_screenshot)
            overlay_path = save_frame_artifact(run_id, step_idx, "som_overlay", png_overlay)

            step_obj = Step(
                n=step_idx,
                ts=datetime.utcnow().isoformat(),
                goal_progress=goal_progress,
                perception={
                    "screenshot": screen_path,
                    "overlay_png": overlay_path,
                    "element_count": len(elements),
                    "state_sig": state_sig
                },
                thought=thought,
                action=Action(type=act_type, element_id=chosen_elem_id, args=args),
                confidence=confidence,
                result={
                    "state_changed": state_changed,
                    "new_state_sig": new_state_sig,
                    "latency_ms": latency_ms
                },
                findings=finding_ids
            )
            self.state.steps.append(step_obj)
            save_step(run_id, step_obj.to_dict())

            # Check if stuck
            if consecutive_dead_clicks >= 3 or consecutive_low_confidence >= 3:
                self.state.status = "stuck"
                self.emit_event_sync("await_input", {
                    "reason": "Agent is stuck after consecutive dead clicks or low confidence. Human intervention recommended.",
                    "step_n": step_idx
                })
                self.pause()

            # Check for completion
            if believes_done or act_type == "done":
                break

            time.sleep(0.4) # pace slightly for observer stability

        # Wrap up run
        self.state.status = "completed" if not self._stop_requested else "stopped"
        metrics = self._calculate_metrics()
        update_run_status(run_id, self.state.status, metrics)
        
        self.emit_event_sync("status", {
            "status": self.state.status,
            "metrics": metrics
        })

        return self.state

    def _execute_human_action(self, action_data: Dict[str, Any], step_n: int):
        """Execute a human click or keystroke through driver during Pause/Take Control."""
        act_type = action_data.get("type", "tap")
        run_id = self.state.run_id

        if act_type == "tap":
            x = int(action_data.get("x", 0))
            y = int(action_data.get("y", 0))
            self.driver.tap(x, y)
        elif act_type == "type":
            text = action_data.get("text", "")
            self.driver.type(text)
        elif act_type == "key":
            name = action_data.get("name", "Enter")
            self.driver.key(name)

        self.emit_event_sync("human_intervention", {
            "action": action_data,
            "step_n": step_n,
            "ts": datetime.utcnow().isoformat()
        })

    def _calculate_metrics(self) -> Dict[str, Any]:
        duration = round(time.time() - self.state.start_time, 2)
        total_steps = len(self.state.steps)
        total_findings = len(self.state.findings)
        
        crit_count = sum(1 for f in self.state.findings if f.severity == "critical")
        major_count = sum(1 for f in self.state.findings if f.severity == "major")
        minor_count = sum(1 for f in self.state.findings if f.severity == "minor")

        # Composite friction score: 100 - (crit*15 + major*8 + minor*2) capped at 0
        friction_penalty = (crit_count * 15) + (major_count * 8) + (minor_count * 2)
        friction_score = max(10, 100 - friction_penalty)

        # Accessibility score
        a11y_violations = sum(1 for f in self.state.findings if f.category == "a11y")
        a11y_score = max(15, 100 - (a11y_violations * 12))

        cost_info = llm_client.compute_cost()

        return {
            "duration_sec": duration,
            "total_steps": total_steps,
            "optimal_steps": max(2, int(total_steps * 0.7)),
            "total_findings": total_findings,
            "critical_count": crit_count,
            "major_count": major_count,
            "minor_count": minor_count,
            "friction_score": friction_score,
            "a11y_score": a11y_score,
            "human_interventions": self.state.interventions,
            "cost_usd": cost_info["total_cost_usd"]
        }
