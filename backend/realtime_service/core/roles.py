"""This service's seam onto the shared role enum. [F-6] #76

Callers import it from here rather than from `shared.roles`, so `service` and
`model` reach no further than `core`. ADR 0012.
"""

from __future__ import annotations

from shared.roles import UserRole

__all__ = ["UserRole"]
