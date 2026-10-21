"""Feature package boundary.

Responsibilities:
- Compute feature values from preprocessed waveform segments.

In scope:
- Feature math and rolling statistics needed by detection.

Out of scope:
- TCP IO, plotting, and persistent storage.
"""

from .feature_calculator import FeatureCalculator

__all__ = ['FeatureCalculator']