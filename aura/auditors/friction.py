import uuid
import yaml
import os
import io
import numpy as np
from PIL import Image
from typing import List, Dict, Any, Optional
from playwright.sync_api import Page
from aura.drivers.base import Element
from aura.agent.state import Finding
from aura.agent.perception import crop_element_evidence, save_frame_artifact

RULES_PATH = os.path.join(os.path.dirname(__file__), "rules.yaml")

def load_rules() -> Dict[str, Any]:
    try:
        with open(RULES_PATH, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}

class FrictionAuditor:
    """Audits interaction friction across layout shifts, occlusions, touch targets, clutter, loops, and dead clicks."""

    def __init__(self):
        self.rules = load_rules().get("friction", {})
        self.min_touch_w = self.rules.get("touch_target_min_width", 44)
        self.min_touch_h = self.rules.get("touch_target_min_height", 44)
        self.min_spacing = self.rules.get("touch_target_spacing_min", 8)
        self.max_clutter_elems = self.rules.get("visual_clutter_max_elements", 35)
        self.slow_threshold_ms = self.rules.get("slow_response_threshold_ms", 1500)

    def audit_step(
        self,
        page: Optional[Page],
        elements: List[Element],
        pre_screenshot: bytes,
        post_screenshot: bytes,
        target_element: Optional[Element],
        action_type: str,
        latency_ms: int,
        state_changed: bool,
        consecutive_dead_clicks: int,
        loop_count: int,
        run_id: str,
        step_n: int
    ) -> List[Finding]:
        findings: List[Finding] = []

        # 1. Touch-target size checks on all visible interactables
        findings.extend(self._check_touch_targets(elements, pre_screenshot, run_id, step_n))

        # 2. Occlusion check on target element
        if target_element and target_element.occluded_by:
            findings.extend(self._check_occlusion(target_element, pre_screenshot, run_id, step_n))

        # 3. Dead click / no state change detection
        if action_type == "tap" and not state_changed:
            findings.extend(self._check_dead_click(target_element, pre_screenshot, consecutive_dead_clicks, run_id, step_n))

        # 4. Redundant navigation loop
        if loop_count >= self.rules.get("max_nav_loop_repetitions", 2):
            findings.extend(self._check_navigation_loop(loop_count, pre_screenshot, run_id, step_n))

        # 5. Slow response time
        if latency_ms > self.slow_threshold_ms:
            findings.append(Finding(
                id=f"f_{uuid.uuid4().hex[:8]}",
                run_id=run_id,
                step_n=step_n,
                category="friction",
                rule_id="friction:slow-response",
                severity="minor",
                title=f"Slow UI interaction response ({latency_ms}ms > {self.slow_threshold_ms}ms threshold)",
                evidence={"measured": latency_ms, "threshold": self.slow_threshold_ms, "unit": "ms"},
                element={"bbox": target_element.bbox if target_element else [0,0,0,0], "role": "page", "name": "Action Latency"},
                screenshot=save_frame_artifact(run_id, step_n, "screen", post_screenshot),
                cropped_png="",
                personas=["cognitive"],
                recommendation=f"Optimize event handler or backend API to keep response time below {self.slow_threshold_ms}ms.",
                wishlisted=False
            ))

        # 6. Layout shift check
        findings.extend(self._check_layout_shift(page, pre_screenshot, post_screenshot, target_element, run_id, step_n))

        # 7. Visual clutter check
        findings.extend(self._check_visual_clutter(elements, pre_screenshot, run_id, step_n))

        return findings

    def _check_touch_targets(self, elements: List[Element], screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        findings = []
        for elem in elements:
            w, h = elem.bbox[2], elem.bbox[3]
            # Flag if interactable is smaller than 44x44
            if elem.role in ["button", "link", "input", "icon-btn", "a"] and (w < self.min_touch_w or h < self.min_touch_h) and w > 0 and h > 0:
                crop = crop_element_evidence(screenshot, elem.bbox)
                crop_url = save_frame_artifact(run_id, step_n, f"crop_target_{elem.id}", crop) if crop else ""
                screen_url = save_frame_artifact(run_id, step_n, "screen", screenshot)

                findings.append(Finding(
                    id=f"f_{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    step_n=step_n,
                    category="friction",
                    rule_id="friction:touch-target-min",
                    severity="major" if (w < 30 or h < 30) else "minor",
                    title=f"Sub-standard hit target size: Mark #{elem.id} is {w}x{h}px (minimum {self.min_touch_w}x{self.min_touch_h}px required)",
                    evidence={"measured": f"{w}x{h}", "threshold": f"{self.min_touch_w}x{self.min_touch_h}", "unit": "px"},
                    element={"bbox": elem.bbox, "role": elem.role, "name": elem.name, "selector_hint": f"#{elem.id}"},
                    screenshot=screen_url,
                    cropped_png=crop_url,
                    personas=["motor", "cognitive"],
                    recommendation=f"Expand touch bounding target from {w}x{h}px to at least {self.min_touch_w}x{self.min_touch_h}px using padding or min-height.",
                    wishlisted=False
                ))
                if len(findings) >= 3:
                    break # Cap to top 3 instances per step to avoid overwhelming
        return findings

    def _check_occlusion(self, element: Element, screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        crop = crop_element_evidence(screenshot, element.bbox)
        crop_url = save_frame_artifact(run_id, step_n, f"crop_occlusion_{element.id}", crop) if crop else ""
        screen_url = save_frame_artifact(run_id, step_n, "screen", screenshot)

        return [Finding(
            id=f"f_{uuid.uuid4().hex[:8]}",
            run_id=run_id,
            step_n=step_n,
            category="friction",
            rule_id="friction:occluded-control",
            severity="critical",
            title=f"Actionable control #{element.id} ('{element.name}') is occluded by {element.occluded_by}",
            evidence={"measured": f"Occluded by {element.occluded_by}", "threshold": "Unobstructed", "unit": "hit_test"},
            element={"bbox": element.bbox, "role": element.role, "name": element.name, "selector_hint": f"#{element.id}"},
            screenshot=screen_url,
            cropped_png=crop_url,
            personas=["motor", "cognitive"],
            recommendation=f"Ensure modal or overlay container {element.occluded_by} does not intercept pointer-events or block primary checkout flows.",
            wishlisted=False
        )]

    def _check_dead_click(self, element: Optional[Element], screenshot: bytes, count: int, run_id: str, step_n: int) -> List[Finding]:
        bbox = element.bbox if element else [0, 0, 0, 0]
        name = element.name if element else "Unspecified element"
        crop = crop_element_evidence(screenshot, bbox) if bbox[2] > 0 else b""
        crop_url = save_frame_artifact(run_id, step_n, "crop_dead_click", crop) if crop else ""
        screen_url = save_frame_artifact(run_id, step_n, "screen", screenshot)

        return [Finding(
            id=f"f_{uuid.uuid4().hex[:8]}",
            run_id=run_id,
            step_n=step_n,
            category="friction",
            rule_id="friction:dead-click",
            severity="major",
            title=f"Dead click registered on '{name}': Tap triggered zero UI or state change",
            evidence={"measured": f"{count} consecutive dead clicks", "threshold": "State change expected", "unit": "clicks"},
            element={"bbox": bbox, "role": element.role if element else "control", "name": name, "selector_hint": name},
            screenshot=screen_url,
            cropped_png=crop_url,
            personas=["motor", "cognitive"],
            recommendation="Verify click handler responds with visual feedback (e.g. active state, loading spinner, or navigation).",
            wishlisted=False
        )]

    def _check_navigation_loop(self, loop_count: int, screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        screen_url = save_frame_artifact(run_id, step_n, "screen", screenshot)
        return [Finding(
            id=f"f_{uuid.uuid4().hex[:8]}",
            run_id=run_id,
            step_n=step_n,
            category="friction",
            rule_id="friction:redundant-navigation-loop",
            severity="critical",
            title=f"Redundant navigation loop detected: Re-entered identical state {loop_count} times",
            evidence={"measured": loop_count, "threshold": self.rules.get("max_nav_loop_repetitions", 2), "unit": "loops"},
            element={"bbox": [0,0,0,0], "role": "journey", "name": "Cyclic Navigation Flow"},
            screenshot=screen_url,
            cropped_png="",
            personas=["cognitive"],
            recommendation="Eliminate circular navigation redirects and provide direct access to destination screens.",
            wishlisted=False
        )]

    def _check_layout_shift(self, page: Optional[Page], pre_bytes: bytes, post_bytes: bytes, target_elem: Optional[Element], run_id: str, step_n: int) -> List[Finding]:
        findings = []
        if not page or not pre_bytes or not post_bytes:
            return findings

        # 1. Read PerformanceObserver layout shifts
        try:
            cls_shifts = page.evaluate("() => window.__layoutShifts || []")
            if cls_shifts:
                total_cls = sum(cls_shifts)
                threshold = self.rules.get("layout_shift_threshold", 0.05)
                if total_cls > threshold:
                    findings.append(Finding(
                        id=f"f_{uuid.uuid4().hex[:8]}",
                        run_id=run_id,
                        step_n=step_n,
                        category="friction",
                        rule_id="friction:layout-shift",
                        severity="major",
                        title=f"Unstable layout shift detected (CLS: {total_cls:.3f} > {threshold})",
                        evidence={"measured": round(total_cls, 4), "threshold": threshold, "unit": "CLS"},
                        element={"bbox": [0,0,0,0], "role": "viewport", "name": "Layout Shift"},
                        screenshot=save_frame_artifact(run_id, step_n, "screen", post_bytes),
                        cropped_png="",
                        personas=["motor", "cognitive"],
                        recommendation="Set explicit dimensions (aspect-ratio, width/height) on dynamically loaded elements to prevent layout shifting.",
                        wishlisted=False
                    ))
        except Exception:
            pass

        return findings

    def _check_visual_clutter(self, elements: List[Element], screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        findings = []
        count = len(elements)
        if count > self.max_clutter_elems:
            findings.append(Finding(
                id=f"f_{uuid.uuid4().hex[:8]}",
                run_id=run_id,
                step_n=step_n,
                category="friction",
                rule_id="friction:visual-clutter",
                severity="minor",
                title=f"Excessive visual density: {count} interactables in viewport (recommended max: {self.max_clutter_elems})",
                evidence={"measured": count, "threshold": self.max_clutter_elems, "unit": "elements"},
                element={"bbox": [0,0,0,0], "role": "viewport", "name": "Viewport Density"},
                screenshot=save_frame_artifact(run_id, step_n, "screen", screenshot),
                cropped_png="",
                personas=["cognitive", "dyslexia"],
                recommendation="Reduce simultaneous call-to-actions and introduce progressive disclosure for secondary actions.",
                wishlisted=False
            ))
        return findings

friction_auditor = FrictionAuditor()
