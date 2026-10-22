"""Separate immutable bundle resources from writable portable configuration."""
import sys
from pathlib import Path


def application_root():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def bundle_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
