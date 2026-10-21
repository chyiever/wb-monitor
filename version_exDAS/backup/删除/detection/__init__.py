"""Detection package boundary.

Responsibilities:
- Convert feature streams into detection events using threshold logic.

In scope:
- Baseline/threshold management and event state transitions.

Out of scope:
- Raw signal filtering/downsampling and UI rendering.
"""

from .threshold_detector import ThresholdDetector
