"""Application entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .logger import get_logger, install_excepthook, setup_logging
from .viewer import ReplayWindow


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FIP/eDAS joint NPZ replay viewer")
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Joint data file or directory. Default: last-used directory, "
        "then D:/PCCP/FIPeDASDATA if it exists, otherwise cwd.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    install_excepthook()
    logger = get_logger()

    args = parse_args(argv)
    initial_path = Path(args.path) if args.path else None
    app = QApplication(sys.argv[:1])
    logger.info("启动 %s %s", ReplayWindow.APP_NAME, ReplayWindow.APP_VERSION)
    window = ReplayWindow(initial_path=initial_path)
    window.show()
    exit_code = int(app.exec_())
    logger.info("退出，代码 %s", exit_code)
    return exit_code
