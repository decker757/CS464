"""This service's handle on the shared role vocabulary. [F-6] #76

Re-exported rather than redefined. Until #76 these five files were five copies
of one enum, kept in step by hand and failing closed when they drifted — a
service that had not heard of a role treated its holder as a TRADER, which is
silent by design and therefore silent when it is wrong.

The module stays, rather than every caller importing `shared.roles` directly,
because the layering rule in CLAUDE.md is that `service` and `model` reach into
`core` and no further. This is the seam that keeps that true.
"""

from __future__ import annotations

from shared.roles import UserRole

__all__ = ["UserRole"]
