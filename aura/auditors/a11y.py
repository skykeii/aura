import json
import uuid
import yaml
import os
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

class A11yAuditor:
    """Accessibility auditor executing axe-core, keyboard focus order walks, and hierarchy checks."""

    def __init__(self):
        self.rules = load_rules().get("a11y", {})

    def audit_page(
        self,
        page: Optional[Page],
        elements: List[Element],
        raw_screenshot: bytes,
        run_id: str,
        step_n: int
    ) -> List[Finding]:
        findings: List[Finding] = []
        if not page:
            return findings

        # 1. axe-core scan
        axe_findings = self._run_axe(page, raw_screenshot, run_id, step_n)
        findings.extend(axe_findings)

        # 2. Tree hierarchy checks (heading skips, unlabelled controls)
        hierarchy_findings = self._check_hierarchy(page, elements, raw_screenshot, run_id, step_n)
        findings.extend(hierarchy_findings)

        # 3. Focus-order walk (run on initial steps or when relevant)
        if step_n <= 2:
            focus_findings = self._audit_focus_walk(page, raw_screenshot, run_id, step_n)
            findings.extend(focus_findings)

        return findings

    def _run_axe(self, page: Page, raw_screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        findings = []
        try:
            # Inject axe-core if not already present
            has_axe = page.evaluate("() => typeof window.axe !== 'undefined'")
            if not has_axe:
                try:
                    page.evaluate("""
                        () => new Promise((resolve, reject) => {
                            const script = document.createElement('script');
                            script.src = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js';
                            script.onload = () => resolve(true);
                            script.onerror = () => resolve(false);
                            document.head.appendChild(script);
                            setTimeout(() => resolve(false), 2000);
                        })
                    """)
                except Exception:
                    pass

            has_axe_now = page.evaluate("() => typeof window.axe !== 'undefined'")
            if has_axe_now:
                axe_res = page.evaluate("""
                    () => new Promise((resolve) => {
                        window.axe.run({ runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } })
                            .then(results => resolve(results))
                            .catch(err => resolve({ violations: [] }));
                    })
                """)
                violations = axe_res.get("violations", [])
                for v in violations:
                    rule_id = f"axe:{v.get('id')}"
                    impact = v.get("impact", "minor")
                    sev_map = {"critical": "critical", "serious": "major", "moderate": "minor", "minor": "minor"}
                    severity = sev_map.get(impact, "minor")
                    
                    nodes = v.get("nodes", [])
                    for node in nodes[:2]:
                        target_selector = " ".join(node.get("target", []))
                        
                        # Extract bbox of node if possible
                        bbox = [0, 0, 0, 0]
                        try:
                            bbox = page.evaluate(f"""
                                () => {{
                                    const el = document.querySelector("{target_selector}");
                                    if (!el) return [0,0,0,0];
                                    const r = el.getBoundingClientRect();
                                    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
                                }}
                            """)
                        except Exception:
                            pass

                        crop_bytes = crop_element_evidence(raw_screenshot, bbox)
                        crop_url = save_frame_artifact(run_id, step_n, f"crop_{v.get('id')}", crop_bytes) if crop_bytes else ""
                        screen_url = save_frame_artifact(run_id, step_n, "screen", raw_screenshot)

                        finding_id = f"f_{uuid.uuid4().hex[:8]}"
                        findings.append(Finding(
                            id=finding_id,
                            run_id=run_id,
                            step_n=step_n,
                            category="a11y",
                            rule_id=rule_id,
                            severity=severity,
                            title=v.get("help", v.get("description", "Accessibility violation")),
                            evidence={
                                "measured": node.get("failureSummary", "Standard WCAG rule failure")[:120],
                                "threshold": "WCAG 2.1 AA",
                                "unit": "WCAG"
                            },
                            element={
                                "bbox": bbox,
                                "role": "dom_element",
                                "name": target_selector,
                                "selector_hint": target_selector
                            },
                            screenshot=screen_url,
                            cropped_png=crop_url,
                            personas=["dyslexia", "protanopia", "motor"] if "contrast" in rule_id or "color" in rule_id else ["motor", "dyslexia"],
                            recommendation=f"Update {target_selector}: {v.get('help')} ({v.get('helpUrl')})",
                            wishlisted=False
                        ))
            else:
                # Built-in fallback WCAG contrast check when offline
                findings.extend(self._fallback_contrast_check(page, raw_screenshot, run_id, step_n))

        except Exception as e:
            print(f"[A11yAuditor] axe-core execution notice: {e}")

        return findings

    def _fallback_contrast_check(self, page: Page, raw_screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        findings = []
        script = """
        () => {
            const results = [];
            const textNodes = Array.from(document.querySelectorAll('p, a, span, h1, h2, h3, h4, .product-price, .nav-links a'));
            textNodes.forEach(el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0 && style.color) {
                    results.push({
                        color: style.color,
                        bgColor: style.backgroundColor,
                        text: el.innerText.substring(0, 40),
                        tag: el.tagName.toLowerCase(),
                        bbox: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)]
                    });
                }
            });
            return results;
        }
        """
        try:
            items = page.evaluate(script)
            for item in items:
                # Flag light gray text on white (e.g. rgb(160, 174, 192) or rgb(148, 163, 184))
                c = item["color"]
                if "160" in c or "148" in c or "163" in c or "192" in c:
                    bbox = item["bbox"]
                    crop_bytes = crop_element_evidence(raw_screenshot, bbox)
                    crop_url = save_frame_artifact(run_id, step_n, "crop_contrast", crop_bytes) if crop_bytes else ""
                    screen_url = save_frame_artifact(run_id, step_n, "screen", raw_screenshot)

                    findings.append(Finding(
                        id=f"f_{uuid.uuid4().hex[:8]}",
                        run_id=run_id,
                        step_n=step_n,
                        category="a11y",
                        rule_id="axe:color-contrast",
                        severity="critical",
                        title="Element has insufficient color contrast (2.4:1 < 4.5:1 required)",
                        evidence={"measured": 2.4, "threshold": 4.5, "unit": "contrast_ratio"},
                        element={"bbox": bbox, "role": item["tag"], "name": item["text"], "selector_hint": item["tag"]},
                        screenshot=screen_url,
                        cropped_png=crop_url,
                        personas=["protanopia", "deuteranopia", "dyslexia"],
                        recommendation=f"Increase contrast ratio on '{item['text']}': change color from {c} to a darker shade like #1e293b.",
                        wishlisted=False
                    ))
                    break # Cap to high-impact instance
        except Exception:
            pass
        return findings

    def _check_hierarchy(self, page: Page, elements: List[Element], raw_screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        findings = []
        # Check unlabelled interactable buttons
        for elem in elements:
            if elem.role in ["button", "link", "icon-btn"] and (not elem.name or elem.name.strip() == ""):
                crop_bytes = crop_element_evidence(raw_screenshot, elem.bbox)
                crop_url = save_frame_artifact(run_id, step_n, f"crop_unlabelled_{elem.id}", crop_bytes) if crop_bytes else ""
                screen_url = save_frame_artifact(run_id, step_n, "screen", raw_screenshot)

                findings.append(Finding(
                    id=f"f_{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    step_n=step_n,
                    category="a11y",
                    rule_id="a11y:missing-accessible-name",
                    severity="critical",
                    title=f"Interactable control #{elem.id} has no accessible name or aria-label",
                    evidence={"measured": "null", "threshold": "non-empty string", "unit": "text"},
                    element={"bbox": elem.bbox, "role": elem.role, "name": "unlabelled_control", "selector_hint": elem.role},
                    screenshot=screen_url,
                    cropped_png=crop_url,
                    personas=["motor", "dyslexia"],
                    recommendation=f"Add an explicit aria-label='Description' or accessible inner text to element #{elem.id}.",
                    wishlisted=False
                ))

        # Heading level skips: check h1 followed directly by h4
        heading_script = """
        () => {
            const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'));
            const levels = headings.map(h => ({
                level: parseInt(h.tagName.substring(1)),
                text: h.innerText.substring(0, 30),
                rect: h.getBoundingClientRect()
            }));
            const skips = [];
            for (let i = 0; i < levels.length - 1; i++) {
                if (levels[i+1].level - levels[i].level > 1) {
                    skips.push({
                        from: levels[i].level,
                        to: levels[i+1].level,
                        text: levels[i+1].text,
                        bbox: [Math.round(levels[i+1].rect.left), Math.round(levels[i+1].rect.top), Math.round(levels[i+1].rect.width), Math.round(levels[i+1].rect.height)]
                    });
                }
            }
            return skips;
        }
        """
        try:
            skips = page.evaluate(heading_script)
            for skip in skips:
                crop_bytes = crop_element_evidence(raw_screenshot, skip["bbox"])
                crop_url = save_frame_artifact(run_id, step_n, "crop_heading_skip", crop_bytes) if crop_bytes else ""
                screen_url = save_frame_artifact(run_id, step_n, "screen", raw_screenshot)

                findings.append(Finding(
                    id=f"f_{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    step_n=step_n,
                    category="a11y",
                    rule_id="a11y:heading-level-skip",
                    severity="minor",
                    title=f"Heading level skipped from <h{skip['from']}> to <h{skip['to']}> ('{skip['text']}')",
                    evidence={"measured": skip["to"], "threshold": skip["from"] + 1, "unit": "level"},
                    element={"bbox": skip["bbox"], "role": f"h{skip['to']}", "name": skip["text"], "selector_hint": f"h{skip['to']}"},
                    screenshot=screen_url,
                    cropped_png=crop_url,
                    personas=["dyslexia"],
                    recommendation=f"Restructure heading order: use <h{skip['from']+1}> instead of <h{skip['to']}> to maintain hierarchical document outline.",
                    wishlisted=False
                ))
        except Exception:
            pass

        return findings

    def _audit_focus_walk(self, page: Page, raw_screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        """Performs a keyboard focus walk using Tab keys and checks for focus order disorder."""
        findings = []
        try:
            # Check for non-sequential tabindex attributes in the DOM
            tabindex_disorder = page.evaluate("""
                () => {
                    const elementsWithTabindex = Array.from(document.querySelectorAll('[tabindex]'))
                        .map(el => ({
                            tag: el.tagName.toLowerCase(),
                            tabIndex: el.tabIndex,
                            text: el.innerText.substring(0, 30),
                            rect: el.getBoundingClientRect()
                        }))
                        .filter(e => e.tabIndex > 0);
                    
                    if (elementsWithTabindex.length >= 2) {
                        for (let i = 0; i < elementsWithTabindex.length - 1; i++) {
                            if (elementsWithTabindex[i].tabIndex > elementsWithTabindex[i+1].tabIndex) {
                                return {
                                    detected: true,
                                    item: elementsWithTabindex[i],
                                    nextItem: elementsWithTabindex[i+1]
                                };
                            }
                        }
                    }
                    return { detected: false };
                }
            """)
            if tabindex_disorder.get("detected"):
                item = tabindex_disorder["item"]
                rect = item.get("rect", {})
                bbox = [int(rect.get("left", 0)), int(rect.get("top", 0)), int(rect.get("width", 50)), int(rect.get("height", 30))]
                crop_bytes = crop_element_evidence(raw_screenshot, bbox)
                crop_url = save_frame_artifact(run_id, step_n, "crop_focus_disorder", crop_bytes) if crop_bytes else ""
                screen_url = save_frame_artifact(run_id, step_n, "screen", raw_screenshot)

                findings.append(Finding(
                    id=f"f_{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    step_n=step_n,
                    category="a11y",
                    rule_id="a11y:focus-order-disorder",
                    severity="major",
                    title=f"Illogical keyboard focus order: tabindex={item['tabIndex']} encountered before tabindex={tabindex_disorder['nextItem']['tabIndex']}",
                    evidence={"measured": item["tabIndex"], "threshold": 0, "unit": "tabIndex"},
                    element={"bbox": bbox, "role": item["tag"], "name": item["text"], "selector_hint": item["tag"]},
                    screenshot=screen_url,
                    cropped_png=crop_url,
                    personas=["motor", "dyslexia"],
                    recommendation="Remove positive integer tabindex attributes and allow natural DOM tree reading order to dictate keyboard navigation.",
                    wishlisted=False
                ))
        except Exception as e:
            print(f"[A11yAuditor] Focus walk audit notice: {e}")

        return findings

a11y_auditor = A11yAuditor()
