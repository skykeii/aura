import hashlib
import io
import time
from typing import List, Dict, Any, Optional
from PIL import Image
from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page
from aura.drivers.base import Driver, Element

class WebDriver(Driver):
    """Playwright-backed browser driver for desktop and mobile web profiles.
    Strictly black-box: interacts strictly by coordinates and keyboard events.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._mode: str = "desktop"
        self._viewport_config: Dict[str, Any] = {"width": 1280, "height": 800}

    def start(self, target: str, profile: Optional[Dict[str, Any]] = None) -> None:
        profile = profile or {}
        self._mode = profile.get("mode", "desktop")

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )

        if self._mode == "mobile_web":
            self._viewport_config = {"width": 393, "height": 852}
            self._context = self._browser.new_context(
                viewport={"width": 393, "height": 852},
                device_scale_factor=2,
                is_mobile=True,
                has_touch=True,
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            )
        else:
            self._viewport_config = {"width": 1280, "height": 800}
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False
            )

        self._page = self._context.new_page()
        # Inject layout shift observer early
        self._page.add_init_script("""
            window.__layoutShifts = [];
            try {
                const observer = new PerformanceObserver((list) => {
                    for (const entry of list.getEntries()) {
                        if (!entry.hadRecentInput) {
                            window.__layoutShifts.push(entry.value);
                        }
                    }
                });
                observer.observe({type: 'layout-shift', buffered: true});
            } catch (e) {}
        """)

        try:
            self._page.goto(target, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass
        time.sleep(1.0) # settle

    def screenshot(self) -> bytes:
        if not self._page:
            return b""
        for attempt in range(3):
            try:
                return self._page.screenshot(type="png", full_page=False)
            except Exception:
                try:
                    self._page.wait_for_load_state("domcontentloaded", timeout=2000)
                except Exception:
                    pass
                time.sleep(0.4)
        return b""

    def viewport(self) -> Dict[str, Any]:
        dpr = 2 if self._mode == "mobile_web" else 1
        return {
            "w": self._viewport_config["width"],
            "h": self._viewport_config["height"],
            "dpr": dpr,
            "mode": self._mode
        }

    def elements(self) -> List[Element]:
        if not self._page:
            return []

        js_script = """
        () => {
            const results = [];
            const selector = [
                'button', 'a', 'input', 'select', 'textarea',
                '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
                '[role="tab"]', '[role="menuitem"]', '[role="option"]', '[role="switch"]',
                '[role="searchbox"]', '[role="combobox"]', '[role="search"]',
                '[tabindex]:not([tabindex="-1"])', '[onclick]',
                'video', '[role="video"]',
                'summary', 'details',
                'ytd-video-renderer a#video-title', 'ytd-rich-item-renderer a#video-title',
                'ytd-thumbnail', 'a.ytd-thumbnail',
                'article a', '[role="article"] a',
                'h1 a', 'h2 a', 'h3 a', 'h4 a',
                '[contenteditable="true"]',
                '[data-action]', '[data-click]'
            ].join(', ');
            
            const nodes = Array.from(document.querySelectorAll(selector));
            const seen = new Set();
            
            nodes.forEach((el) => {
                if (seen.has(el)) return;
                seen.add(el);

                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                
                // Filter out non-rendered or out of viewport elements
                if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                    return;
                }
                
                const cx = Math.round(rect.left + rect.width / 2);
                const cy = Math.round(rect.top + rect.height / 2);
                
                // Viewport boundary check
                if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) {
                    return;
                }

                // Hit testing to check occlusion
                let occludedBy = null;
                let visibleRatio = 1.0;
                if (cx >= 0 && cx <= window.innerWidth && cy >= 0 && cy <= window.innerHeight) {
                    const topEl = document.elementFromPoint(cx, cy);
                    if (topEl && topEl !== el && !el.contains(topEl) && !topEl.contains(el)) {
                        const topStyle = window.getComputedStyle(topEl);
                        if (topStyle.pointerEvents !== 'none' && parseFloat(topStyle.opacity || '1') > 0.05) {
                            occludedBy = topEl.tagName.toLowerCase() + (topEl.className ? '.' + topEl.className.toString().split(' ')[0] : '');
                            visibleRatio = 0.0;
                        }
                    }
                }

                // Compute accessible name & role
                let name = el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') || el.getAttribute('placeholder') || el.getAttribute('value') || '';
                if (!name && el.tagName.toLowerCase() === 'video') {
                    name = 'Active Video Player';
                }
                if (!name) {
                    const inner = el.querySelector('[aria-label], [title], h1, h2, h3, span, p');
                    if (inner) {
                        name = inner.getAttribute('aria-label') || inner.getAttribute('title') || inner.innerText || '';
                    }
                }
                name = name.trim().replace(/\\s+/g, ' ').substring(0, 100);
                
                const role = el.getAttribute('role') || el.tagName.toLowerCase();
                const focusable = el.tabIndex >= 0;

                results.push({
                    bbox: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)],
                    center: [cx, cy],
                    role: role,
                    name: name,
                    value: el.value || null,
                    focusable: focusable,
                    visible_ratio: visibleRatio,
                    occluded_by: occludedBy,
                    text: name,
                    source: "a11y"
                });
            });
            return results;
        }
        """
        raw_items = []
        for _ in range(3):
            try:
                raw_items = self._page.evaluate(js_script)
                break
            except Exception:
                time.sleep(0.4)
        elements = []
        for idx, item in enumerate(raw_items, start=1):
            elements.append(Element(
                id=idx,
                bbox=item["bbox"],
                center=item["center"],
                role=item["role"],
                name=item["name"],
                value=item.get("value"),
                focusable=item["focusable"],
                visible_ratio=item["visible_ratio"],
                occluded_by=item["occluded_by"],
                text=item["text"],
                source=item["source"]
            ))
        return elements

    def a11y_tree(self) -> Dict[str, Any]:
        if not self._page:
            return {}
        try:
            return self._page.accessibility.snapshot() or {}
        except Exception:
            return {}

    def tap(self, x: int, y: int) -> None:
        if not self._page:
            return
        if self._mode == "mobile_web":
            self._page.touchscreen.tap(x, y)
        else:
            self._page.mouse.click(x, y)
        time.sleep(0.4)

    def type(self, text: str) -> None:
        if not self._page:
            return
        self._page.keyboard.type(text, delay=30)
        time.sleep(0.3)

    def scroll(self, dx: int, dy: int) -> None:
        if not self._page:
            return
        self._page.mouse.wheel(dx, dy)
        time.sleep(0.4)

    def key(self, name: str) -> None:
        if not self._page:
            return
        self._page.keyboard.press(name)
        time.sleep(0.3)

    def state_signature(self) -> str:
        """Compute robust multi-signal state signature:
        Combines URL + hash of visible element roles & names + rough image hash.
        """
        if not self._page:
            return "empty"
        try:
            url = self._page.url
            title = self._page.title()
            elems = self.elements()
            elem_sig = "|".join(f"{e.role}:{e.name[:20]}" for e in elems[:20])
            
            # Simple perceptual image hash (downsampled 8x8 average hash)
            raw_bytes = self.screenshot()
            if raw_bytes:
                img = Image.open(io.BytesIO(raw_bytes)).convert('L').resize((8, 8), Image.Resampling.BILINEAR)
                pixels = list(img.getdata())
                avg = sum(pixels) / len(pixels)
                bits = "".join(['1' if p > avg else '0' for p in pixels])
                img_hash = f"{int(bits, 2):016x}"
            else:
                img_hash = "0000000000000000"

            combined = f"{url}:{title}:{elem_sig}:{img_hash}"
            return hashlib.sha256(combined.encode()).hexdigest()[:16]
        except Exception:
            return "unknown_state"

    def current_url(self) -> str:
        if not self._page:
            return ""
        try:
            return self._page.url or ""
        except Exception:
            return ""

    def page_title(self) -> str:
        if not self._page:
            return ""
        try:
            return self._page.title() or ""
        except Exception:
            return ""

    def stop(self) -> None:
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    @property
    def page(self) -> Optional[Page]:
        """Expose page for runtime auditor injection (e.g. axe-core)."""
        return self._page
