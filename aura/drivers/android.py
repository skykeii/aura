import hashlib
import io
import time
from typing import List, Dict, Any, Optional
from PIL import Image
from aura.drivers.base import Driver, Element

class AndroidDriver(Driver):
    """Native Android driver backed by uiautomator2.
    Interacts strictly via coordinate taps, key events, and native accessibility hierarchy.
    """

    def __init__(self, serial: Optional[str] = None):
        self.serial = serial
        self._d = None
        self._connected = False
        self._package = None
        self._w = 1080
        self._h = 2400

    def start(self, target: str, profile: Optional[Dict[str, Any]] = None) -> None:
        self._package = target
        try:
            import uiautomator2 as u2
            if self.serial:
                self._d = u2.connect(self.serial)
            else:
                self._d = u2.connect()
            
            info = self._d.info
            self._w = info.get("displayWidth", 1080)
            self._h = info.get("displayHeight", 2400)
            self._connected = True
            
            if target and not target.startswith("http"):
                self._d.app_start(target)
                time.sleep(2.0)
        except Exception as e:
            # If no real device or emulator is connected, record status
            self._connected = False
            print(f"[AndroidDriver] Note: uiautomator2 device connection not active: {e}")

    def screenshot(self) -> bytes:
        if self._connected and self._d:
            try:
                pil_img = self._d.screenshot()
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                pass
        
        # Fallback synthetic frame if no physical device is attached
        img = Image.new('RGB', (self._w // 2, self._h // 2), color='#1e293b')
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def viewport(self) -> Dict[str, Any]:
        return {
            "w": self._w,
            "h": self._h,
            "dpr": 3,
            "mode": "android"
        }

    def elements(self) -> List[Element]:
        if not self._connected or not self._d:
            return []

        elements: List[Element] = []
        try:
            # Hierarchy inspection via uiautomator2
            nodes = self._d.xpath("//*[@clickable='true' or @focusable='true']").all()
            for idx, node in enumerate(nodes, start=1):
                bounds = node.bounds # (left, top, right, bottom)
                w = bounds[2] - bounds[0]
                h = bounds[3] - bounds[1]
                cx = bounds[0] + w // 2
                cy = bounds[1] + h // 2
                name = node.text or node.attrib.get("content-desc") or node.attrib.get("resource-id", "")
                role = node.attrib.get("class", "android.view.View").split(".")[-1]

                elements.append(Element(
                    id=idx,
                    bbox=[bounds[0], bounds[1], w, h],
                    center=[cx, cy],
                    role=role,
                    name=name,
                    focusable=node.attrib.get("focusable") == "true",
                    visible_ratio=1.0,
                    source="a11y"
                ))
        except Exception as e:
            print(f"[AndroidDriver] Element harvest error: {e}")

        return elements

    def a11y_tree(self) -> Dict[str, Any]:
        if not self._connected or not self._d:
            return {"platform": "android", "status": "disconnected"}
        try:
            return {"xml": self._d.dump_hierarchy()}
        except Exception as e:
            return {"error": str(e)}

    def tap(self, x: int, y: int) -> None:
        if self._connected and self._d:
            self._d.click(x, y)
        time.sleep(0.4)

    def type(self, text: str) -> None:
        if self._connected and self._d:
            self._d.send_keys(text)
        time.sleep(0.3)

    def scroll(self, dx: int, dy: int) -> None:
        if self._connected and self._d:
            # swipe gesture
            sx = self._w // 2
            sy = self._h // 2
            self._d.swipe(sx, sy, sx - dx, sy - dy, 0.2)
        time.sleep(0.4)

    def key(self, name: str) -> None:
        if self._connected and self._d:
            key_map = {"Back": "back", "Home": "home", "Enter": "enter"}
            k = key_map.get(name, name.lower())
            self._d.press(k)
        time.sleep(0.3)

    def state_signature(self) -> str:
        raw = self.screenshot()
        return hashlib.sha256(raw[:1024]).hexdigest()[:16]

    def stop(self) -> None:
        if self._connected and self._d and self._package:
            try:
                self._d.app_stop(self._package)
            except Exception:
                pass
        self._connected = False
