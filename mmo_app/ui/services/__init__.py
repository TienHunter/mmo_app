"""Read/write bridge between the presentation layer and the pipeline core.

Widgets must go through :class:`UIRepository` instead of opening the SQLite
file directly. This keeps business rules in the core while giving the UI a
stable, view-friendly data shape.
"""

from .repository import UIRepository

__all__ = ["UIRepository"]
