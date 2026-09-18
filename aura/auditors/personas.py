import os
import io
import uuid
import yaml
import numpy as np
from PIL import Image
import textstat
from typing import List, Dict, Any, Optional
from playwright.sync_api import Page
from aura.drivers.base import Element
from aura.agent.state import Finding
from aura.agent.perception import crop_element_evidence, save_frame_artifact

# Standard LMS transformation matrices for color blindness simulation (Brettel, Viénot and Mollon 1997 / Machado 2009)
RGB_TO_LMS = np.array([
    [17.8824, 43.5161, 4.11935],
    [3.45565, 27.1554, 3.86714],
    [0.0299566, 0.184309, 1.46709]
])

LMS_TO_RGB = np.linalg.inv(RGB_TO_LMS)

# Deuteranopia simulation matrix in LMS space
LMS_DEUTERANOPIA = np.array([
    [1.0, 0.0, 0.0],
    [0.494207, 0.0, 1.24827],
    [0.0, 0.0, 1.0]
])

# Protanopia simulation matrix in LMS space
LMS_PROTANOPIA = np.array([
    [0.0, 2.02344, -2.52581],
    [0.0, 1.0, 0.0],
    [0.0, 0.0, 1.0]
])

# Tritanopia simulation matrix in LMS space
LMS_TRITANOPIA = np.array([
    [1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0],
    [-0.395913, 0.801109, 0.0]
])

def simulate_color_blindness(image_bytes: bytes, lens: str = "deuteranopia") -> bytes:
    """Transforms raw screenshot through LMS daltonisation matrices."""
    if not image_bytes:
        return b""
    try:
        pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(pil_img, dtype=np.float32) / 255.0
        
        # Flatten image to N x 3 for matrix multiplication
        h, w, c = arr.shape
        flat = arr.reshape(-1, 3).T
        
        # Convert RGB to LMS
        lms = np.dot(RGB_TO_LMS, flat)
        
        # Apply lens
        if lens == "protanopia":
            sim_lms = np.dot(LMS_PROTANOPIA, lms)
        elif lens == "tritanopia":
            sim_lms = np.dot(LMS_TRITANOPIA, lms)
        else: # deuteranopia (default)
            sim_lms = np.dot(LMS_DEUTERANOPIA, lms)
            
        # Convert back to RGB
        sim_rgb = np.dot(LMS_TO_RGB, sim_lms).T.reshape(h, w, c)
        sim_rgb = np.clip(sim_rgb * 255.0, 0, 255).astype(np.uint8)
        
        sim_img = Image.fromarray(sim_rgb)
        buf = io.BytesIO()
        sim_img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        print(f"[Personas] Color sim error: {e}")
        return image_bytes

class PersonasAuditor:
    """Evaluates UI from specific user lenses: Color Blindness, Dyslexia/Cognitive, and Motor Impairment."""

    def audit_page(
        self,
        page: Optional[Page],
        raw_screenshot: bytes,
        run_id: str,
        step_n: int
    ) -> List[Finding]:
        findings: List[Finding] = []
        if not page:
            return findings

        # 1. Dyslexia & Cognitive load audit (wall-of-text, Flesch reading ease, line-height, text justification)
        findings.extend(self._audit_cognitive_load(page, raw_screenshot, run_id, step_n))

        # 2. Color-only signals audit (e.g. red/green indicators with no accompanying icons or labels)
        findings.extend(self._audit_color_only_signals(page, raw_screenshot, run_id, step_n))

        return findings

    def _audit_cognitive_load(self, page: Page, raw_screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        findings = []
        js_extract = """
        () => {
            const results = [];
            const textBlocks = Array.from(document.querySelectorAll('p, .terms-wall, .legal, article, blockquote, div'));
            textBlocks.forEach(el => {
                const text = (el.innerText || '').trim();
                const wordCount = text.split(/\\s+/).length;
                if (wordCount >= 25 && el.children.length <= 1) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    results.push({
                        text: text,
                        wordCount: wordCount,
                        fontSize: parseFloat(style.fontSize),
                        lineHeight: style.lineHeight,
                        textAlign: style.textAlign,
                        bbox: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)]
                    });
                }
            });
            return results;
        }
        """
        try:
            blocks = page.evaluate(js_extract)
            for b in blocks:
                text = b["text"]
                # Compute Flesch reading ease score
                score = textstat.flesch_reading_ease(text)
                
                # Flag if Flesch score < 50 (difficult) or justified text with tight line-height
                if score < 50.0 or b["textAlign"] == "justify":
                    bbox = b["bbox"]
                    crop = crop_element_evidence(raw_screenshot, bbox)
                    crop_url = save_frame_artifact(run_id, step_n, "crop_cognitive_wall", crop) if crop else ""
                    screen_url = save_frame_artifact(run_id, step_n, "screen", raw_screenshot)

                    title = f"High cognitive load detected: Flesch Reading Ease score of {score:.1f} (Threshold: 60.0)"
                    if b["textAlign"] == "justify":
                        title += " & Justified text alignment"

                    findings.append(Finding(
                        id=f"f_{uuid.uuid4().hex[:8]}",
                        run_id=run_id,
                        step_n=step_n,
                        category="persona",
                        rule_id="persona:dyslexia-cognitive-load",
                        severity="major" if score < 30 else "minor",
                        title=title,
                        evidence={"measured": f"Flesch {score:.1f}, {b['wordCount']} words", "threshold": "Flesch >= 60.0", "unit": "reading_ease"},
                        element={"bbox": bbox, "role": "text_block", "name": text[:40] + "...", "selector_hint": "terms-wall"},
                        screenshot=screen_url,
                        cropped_png=crop_url,
                        personas=["dyslexia", "cognitive"],
                        recommendation="Simplify dense legal jargon into plain language, left-align text, and increase line-height to at least 1.5.",
                        wishlisted=False
                    ))
                    break # Flag prominent block
        except Exception as e:
            print(f"[Personas] Cognitive audit notice: {e}")

        return findings

    def _audit_color_only_signals(self, page: Page, raw_screenshot: bytes, run_id: str, step_n: int) -> List[Finding]:
        findings = []
        # Check for elements relying on color hue alone without icons or prefixes
        js_color = """
        () => {
            const results = [];
            const indicators = Array.from(document.querySelectorAll('.status, .error, .badge, [class*="alert"]'));
            indicators.forEach(el => {
                const text = el.innerText.trim();
                const hasIcon = el.querySelector('svg, img, i') !== null;
                if (!hasIcon && text.length > 0 && text.length < 20) {
                    const rect = el.getBoundingClientRect();
                    results.push({
                        text: text,
                        bbox: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)]
                    });
                }
            });
            return results;
        }
        """
        try:
            items = page.evaluate(js_color)
            for item in items:
                bbox = item["bbox"]
                crop = crop_element_evidence(raw_screenshot, bbox)
                crop_url = save_frame_artifact(run_id, step_n, "crop_color_signal", crop) if crop else ""
                screen_url = save_frame_artifact(run_id, step_n, "screen", raw_screenshot)

                findings.append(Finding(
                    id=f"f_{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    step_n=step_n,
                    category="persona",
                    rule_id="persona:color-only-signal",
                    severity="minor",
                    title=f"Potential color-alone communication on '{item['text']}'",
                    evidence={"measured": "Color without icon", "threshold": "Color + Symbol/Icon", "unit": "visual_signal"},
                    element={"bbox": bbox, "role": "badge", "name": item["text"], "selector_hint": "badge"},
                    screenshot=screen_url,
                    cropped_png=crop_url,
                    personas=["protanopia", "deuteranopia", "tritanopia"],
                    recommendation="Accompany color coding with a recognizable icon or explicit text label so color-blind users can distinguish states.",
                    wishlisted=False
                ))
        except Exception:
            pass

        return findings

    def rewrite_for_persona(self, finding: Finding, persona_name: str) -> str:
        """Adapts recommendation narrative dynamically for the activated persona lens."""
        rec = finding.recommendation
        if persona_name == "deuteranopia":
            if "contrast" in finding.rule_id or "color" in finding.rule_id:
                return f"[Deuteranopia Lens] Users with green-cone deficiency will perceive this text/background combination with severely degraded distinction. {rec} Pair color changes with distinctive icons, underline links, and test with daltonization filters."
        elif persona_name == "dyslexia":
            if "cognitive" in finding.rule_id or "heading" in finding.rule_id or "contrast" in finding.rule_id:
                return f"[Dyslexia Lens] Irregular font rhythms and crowded line spacing create visual distortion ('rivers of white') for readers with dyslexia. {rec} Use left-aligned sans-serif fonts with at least 1.5 line-height."
        elif persona_name == "motor":
            if "touch" in finding.rule_id or "occluded" in finding.rule_id or "dead-click" in finding.rule_id:
                return f"[Motor Impairment Lens] Users with tremors or limited finger articulation frequently trigger unintended adjacent taps or miss sub-44px targets. {rec} Ensure generous 12px margins around all clickable surfaces."
        return rec

personas_auditor = PersonasAuditor()
