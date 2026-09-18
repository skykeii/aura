from typing import List, Dict, Any
from aura.drivers.base import Driver, Element

class IOSDriver(Driver):
    """iOS Driver Interface Stub.
    
    HONESTY DISCLOSURE:
    Real iOS automation requires dedicated macOS host with XCUITest and Xcode tooling.
    To preserve time and maintain high fidelity on Web and Android cross-platform flows,
    the iOS driver implements the Driver interface but raises NotImplementedError.
    """

    def start(self, target: str, profile: Dict[str, Any]) -> None:
        raise NotImplementedError(
            "iOS driver is interface-only in this hackathon release. "
            "Supported platforms are Desktop Web, Mobile Web (device emulation), and Native Android (uiautomator2)."
        )

    def screenshot(self) -> bytes:
        raise NotImplementedError("iOS driver not implemented.")

    def viewport(self) -> Dict[str, Any]:
        return {"w": 393, "h": 852, "dpr": 3, "mode": "ios"}

    def elements(self) -> List[Element]:
        raise NotImplementedError("iOS driver not implemented.")

    def a11y_tree(self) -> Dict[str, Any]:
        raise NotImplementedError("iOS driver not implemented.")

    def tap(self, x: int, y: int) -> None:
        raise NotImplementedError("iOS driver not implemented.")

    def type(self, text: str) -> None:
        raise NotImplementedError("iOS driver not implemented.")

    def scroll(self, dx: int, dy: int) -> None:
        raise NotImplementedError("iOS driver not implemented.")

    def key(self, name: str) -> None:
        raise NotImplementedError("iOS driver not implemented.")

    def state_signature(self) -> str:
        return "ios_stub_state"

    def stop(self) -> None:
        pass
