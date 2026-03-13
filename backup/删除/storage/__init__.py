"""Storage package boundary.

Responsibilities:
- Persist detection/monitoring artifacts to disk.

In scope:
- File naming, directory lifecycle, and serialization formats.

Out of scope:
- Detection decisions, signal processing, and UI behavior.
"""

from .detection_storage import DetectionStorage

__all__ = ['DetectionStorage']