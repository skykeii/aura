import io
import zipfile
from typing import Dict

def create_remediation_zip(bundle: Dict[str, str]) -> bytes:
    """Creates in-memory ZIP archive of remediation patch bundle."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("fixes.css", bundle.get("fixes_css", ""))
        zf.writestr("aria-patches.md", bundle.get("aria_patches_md", ""))
        zf.writestr("remediation.md", bundle.get("remediation_md", ""))
        zf.writestr("findings.json", bundle.get("findings_json", ""))
    return buf.getvalue()
