"""
Compatibility helpers for package imports.

This project historically used absolute imports like `from app.config import settings`
when running from inside the `ai_service/` directory (`uvicorn app.main:app`).
For monorepo-style execution from the repository root (`uvicorn ai_service.app.main:app`),
we also expose this package under the legacy `app` module name.
"""

from __future__ import annotations

import sys

# Allow both import styles:
# - from app.<module> import ...
# - from ai_service.app.<module> import ...
sys.modules.setdefault("app", sys.modules[__name__])
