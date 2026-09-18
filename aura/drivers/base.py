from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional

@dataclass
class Element:
    id: int                           # Set-of-Mark overlay number
    bbox: List[int]                   # [x, y, w, h]
    center: List[int]                 # [x, y]
    role: str                         # accessibility tree role or tag
    name: str                         # accessible name or aria-label
    value: Optional[str] = None       # input value if applicable
    focusable: bool = True
    visible_ratio: float = 1.0
    occluded_by: Optional[str] = None
    text: Optional[str] = None
    source: str = "a11y"              # "a11y" | "vision"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class Driver(ABC):
    """Abstract black-box platform driver interface.
    The agent may ONLY call these methods (no framework selectors or proprietary hooks).
    """

    @abstractmethod
    def start(self, target: str, profile: Dict[str, Any]) -> None:
        """Start session on target (URL or native package) with given profile."""
        pass

    @abstractmethod
    def screenshot(self) -> bytes:
        """Capture rendered screen pixels as raw PNG/JPEG bytes."""
        pass

    @abstractmethod
    def viewport(self) -> Dict[str, Any]:
        """Return {w, h, dpr, mode: 'desktop'|'mobile_web'|'android'|'ios'}."""
        pass

    @abstractmethod
    def elements(self) -> List[Element]:
        """Harvest interactive elements using standard accessibility tree + layout boxes."""
        pass

    @abstractmethod
    def a11y_tree(self) -> Dict[str, Any]:
        """Get standard platform accessibility snapshot tree."""
        pass

    @abstractmethod
    def tap(self, x: int, y: int) -> None:
        """Tap or click coordinate (x, y)."""
        pass

    @abstractmethod
    def type(self, text: str) -> None:
        """Type text into currently focused element or active context."""
        pass

    @abstractmethod
    def scroll(self, dx: int, dy: int) -> None:
        """Scroll viewport by offset dx, dy."""
        pass

    @abstractmethod
    def key(self, name: str) -> None:
        """Send key press (Tab, Enter, Escape, Backspace, ArrowDown, etc.)."""
        pass

    @abstractmethod
    def state_signature(self) -> str:
        """Compute state signature for loop and navigation drift detection."""
        pass

    def current_url(self) -> str:
        """Get the current navigation URL or package identifier."""
        return ""

    def page_title(self) -> str:
        """Get the current document or screen title."""
        return ""

    @abstractmethod
    def stop(self) -> None:
        """Tear down driver session."""
        pass

