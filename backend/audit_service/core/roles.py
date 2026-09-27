"""The shared role enum, re-exported. [F-6] #76

A seam, so `service` and `model` import `core` and never `shared`. ADR 0012.
"""

from __future__ import annotations

from shared.roles import UserRole

__all__ = ["UserRole"]
