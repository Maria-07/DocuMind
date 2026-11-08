"""Write real test documents for the browser tests (uses the backend's document builders)."""

import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(backend))

from tests.documents import build_docx, build_pdf, build_xlsx  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
(out / "Security Policy.docx").write_bytes(build_docx())
(out / "Revenue 2026.xlsx").write_bytes(build_xlsx(rows=12))
(out / "Travel Handbook.pdf").write_bytes(build_pdf(["Travel must be booked through the corporate portal. " * 4]))
print(f"fixtures written to {out}")
