"""Compatibility wrapper for alternate API entrypoints.

The canonical FastAPI application lives in `apps.api.main`.
"""

from .main import app

__all__ = ["app"]
