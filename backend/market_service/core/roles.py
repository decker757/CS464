"""This service's seam onto the shared role enum. [F-6] #76

Re-exported so `service` and `model` import from `core`, never from `shared`.
ADR 0012.
"""

from __future__ import annotations

from shared.roles import UserRole

__all__ = ["UserRole"]
