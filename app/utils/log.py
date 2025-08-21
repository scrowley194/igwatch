# app/utils/log.py
# Minimal, reusable logging setup used across igwatch.
# Provides get_logger(name) that configures a singleton console logger and
# optional rotating file logging when LOG_TO_FILE=true.

from __future__ import annotations
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

_INITIALIZED = False


def _init_root() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    try:
        lvl = getattr(logging, level)
    except AttributeError:
        lvl = logging.INFO

    root = logging.getLogger()
    root.setLevel(lvl)

    # Clean existing handlers in case this is reloaded in notebooks/tests
    for h in list(root.handlers):
        root.removeHandler(h)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(lvl)
    ch.setFormatter(logging.Formatter(_DEFAULT_FMT, datefmt=_DEFAULT_DATEFMT))
    root.addHandler(ch)

    # Optional file handler
    if os.getenv("LOG_TO_FILE", "false").lower() == "true":
        log_dir = Path(os.getenv("LOG_DIR", "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "igwatch.log", maxBytes=int(os.getenv("LOG_MAX_BYTES", "1048576")), backupCount=int(os.getenv("LOG_BACKUPS", "5"))
        )
        fh.setLevel(lvl)
        fh.setFormatter(logging.Formatter(_DEFAULT_FMT, datefmt=_DEFAULT_DATEFMT))
        root.addHandler(fh)

    _INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger with consistent formatting/level.

    Usage:
        from .utils.log import get_logger
        logger = get_logger("igwatch")
    """
    _init_root()
    return logging.getLogger(name)
