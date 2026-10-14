"""Local runtime logging for the FIP/eDAS replay viewer."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "fipedas_read"
LOG_FILENAME = "fipedas_read.log"

_configured = False


def default_log_dir() -> Path:
    """Log directory next to the exe when frozen, otherwise in the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"
    return Path(__file__).resolve().parents[2] / "logs"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        log_dir = default_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / LOG_FILENAME,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        pass

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    _configured = True
    logger.info("日志系统已初始化，日志目录: %s", default_log_dir())
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def install_excepthook() -> None:
    logger = get_logger()

    def _hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook
