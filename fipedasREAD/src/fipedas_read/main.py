"""Application entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .viewer import ReplayWindow


def _default_data_dir() -> Path:
    preferred = Path("D:/PCCP/FIPeDASDATA")
    if preferred.exists():
        return preferred
    return Path.cwd()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FIP/eDAS joint NPZ replay viewer")
    parser.add_argument(
        "path",
        nargs="?",
        default=str(_default_data_dir()),
        help="Joint NPZ file or directory. Default: D:/PCCP/FIPeDASDATA if it exists, otherwise cwd.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = QApplication(sys.argv[:1])
    window = ReplayWindow(initial_path=Path(args.path))
    window.show()
    return int(app.exec_())
