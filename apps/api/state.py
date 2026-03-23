"""Compatibility helpers for mock API state.

The canonical mock graph factory lives in `packages.schemas.mock_data`.
"""

from __future__ import annotations

from packages.schemas import create_mock_session


def build_mock_state():
    return create_mock_session()
