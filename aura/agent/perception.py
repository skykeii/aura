import io
import os
import hashlib
from typing import List, Tuple, Dict, Any
from PIL import Image, ImageDraw, ImageFont
from aura.drivers.base import Driver, Element

def get_runs_dir(run_id: str) -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "runs", run_id)
    os.makedirs(base, exist_ok=True)
    return base

def draw_som_overlay(
    raw_screenshot_bytes: bytes,
    elements: List[Element],
    active_element_id: int = None,
    max_marks: int = 60
) -> Tuple[bytes, bytes, Dict[int, List[int]]]:
    """Draws Set-of-Mark (SoM) numbered badges over interactive elements.
    Returns: (compressed_jpeg_bytes, som_overlay_png_bytes, scaled_bboxes_map)
    """
    if not raw_screenshot_bytes:
        # Generate dummy 1024x768 blank
        img = Image.new('RGB', (1024, 768), color=(30, 41, 59))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue(), buf.getvalue(), {}

    orig_image = Image.open(io.BytesIO(raw_screenshot_bytes)).convert("RGBA")
    orig_w, orig_h = orig_image.size
    
    # Scale width to 1024 if larger for VLM efficiency & low latency
    target_w = min(1024, orig_w)
    scale = target_w / float(orig_w)
    target_h = int(orig_h * scale)

    base_resized = orig_image.resize((target_w, target_h), Image.Resampling.BILINEAR)
    overlay = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    # Use default font
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    scaled_bboxes: Dict[int, List[int]] = {}
    
    # Prioritize elements that are named or have interactive roles, then cap at max_marks
    def element_priority(e: Element):
        has_name = 1 if (e.name and len(e.name.strip()) > 0) else 0
        is_key_role = 1 if e.role in ["button", "input", "a", "video", "searchbox", "combobox"] else 0
        not_occluded = 1 if not e.occluded_by else 0
        return (not_occluded, is_key_role, has_name)

    valid_elements = [e for e in elements if e.bbox[2] > 4 and e.bbox[3] > 4]
    # Keep original order ID stability while selecting top candidates
    prioritized_set = set(e.id for e in sorted(valid_elements, key=element_priority, reverse=True)[:max_marks])
    visible_elements = [e for e in valid_elements if e.id in prioritized_set]

    for elem in visible_elements:
        bx = int(elem.bbox[0] * scale)
        by = int(elem.bbox[1] * scale)
        bw = int(elem.bbox[2] * scale)
        bh = int(elem.bbox[3] * scale)
        scaled_bboxes[elem.id] = [bx, by, bw, bh]

        # Draw bounding outline
        is_active = (active_element_id == elem.id)
        box_color = (239, 68, 68, 220) if is_active else (59, 130, 246, 180) # red if active, else blue
        fill_color = (239, 68, 68, 40) if is_active else (59, 130, 246, 25)

        draw.rectangle([bx, by, bx + bw, by + bh], outline=box_color, width=2, fill=fill_color)

        # Draw number badge at top-left of element
        badge_text = str(elem.id)
        badge_w = max(18, len(badge_text) * 8 + 6)
        badge_h = 16
        
        badge_x = max(0, min(bx, target_w - badge_w))
        badge_y = max(0, min(by - badge_h, target_h - badge_h))

        badge_bg = (220, 38, 38, 240) if is_active else (30, 41, 59, 230)
        draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=3, fill=badge_bg)
        draw.text((badge_x + 3, badge_y + 1), badge_text, fill=(255, 255, 255, 255), font=font)

    # Composite overlay onto base image
    annotated = Image.alpha_composite(base_resized, overlay).convert("RGB")

    # Save to JPEG for WebSocket / VLM
    jpeg_buf = io.BytesIO()
    annotated.save(jpeg_buf, format="JPEG", quality=80)
    
    # Save PNG for artifact storage
    png_buf = io.BytesIO()
    annotated.save(png_buf, format="PNG")

    return jpeg_buf.getvalue(), png_buf.getvalue(), scaled_bboxes

def crop_element_evidence(raw_screenshot_bytes: bytes, bbox: List[int], padding: int = 16) -> bytes:
    """Crops a region around an element bounding box for provable evidence."""
    if not raw_screenshot_bytes or not bbox:
        return b""
    try:
        img = Image.open(io.BytesIO(raw_screenshot_bytes))
        w, h = img.size
        x, y, bw, bh = bbox
        
        left = max(0, x - padding)
        top = max(0, y - padding)
        right = min(w, x + bw + padding)
        bottom = min(h, y + bh + padding)

        if right <= left or bottom <= top:
            return b""

        cropped = img.crop((left, top, right, bottom))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b""

def save_frame_artifact(run_id: str, step_n: int, frame_type: str, data: bytes) -> str:
    """Save frame artifact to runs/{run_id}/{frame_type}_{step_n}.png."""
    if not data:
        return ""
    runs_dir = get_runs_dir(run_id)
    ext = "jpg" if frame_type == "frame" else "png"
    filename = f"{frame_type}_step_{step_n}.{ext}"
    path = os.path.join(runs_dir, filename)
    with open(path, "wb") as f:
        f.write(data)
    return f"/runs/{run_id}/{filename}"
