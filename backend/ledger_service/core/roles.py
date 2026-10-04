"""This service's handle on the shared role vocabulary. [F-6] #76

A seam, so `service` and `model` import from `core` and no further (ADR 0012).
"""

from __future__ import annotations

from shared.roles import UserRole

__all__ = ["UserRole"]
