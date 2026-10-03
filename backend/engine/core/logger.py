"""Structured application logging utilities.

All modules must obtain their logger via `setup_logger(__name__)` instead of
using raw `print()` statements (see 03_DEVELOPMENT_RULES.md, Rule 7).
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED_LOGGERS: set[str] = set()

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(
    name: str,
    level: str = "INFO",
    log_dir: str | None = None,
    log_to_file: bool = True,
    log_to_console: bool = True,
    max_bytes: int = 5_242_880,
    backup_count: int = 3,
) -> logging.Logger:
    """Initializes (or retrieves) a structured logger.

    Args:
        name: Logger name, typically the calling module's `__name__`.
        level: Logging level string (e.g. "DEBUG", "INFO", "WARNING", "ERROR").
        log_dir: Directory in which to write rotating log files. Required if
            `log_to_file` is True.
        log_to_file: Whether to attach a rotating file handler.
        log_to_console: Whether to attach a console (stdout) handler.
        max_bytes: Maximum size in bytes before a log file rotates.
        backup_count: Number of rotated backup log files to retain.

    Returns:
        A configured `logging.Logger` instance.
    """
    logger = logging.getLogger(name)

    if name in _CONFIGURED_LOGGERS:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    if log_to_console:
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if log_to_file and log_dir is not None:
        log_directory = Path(log_dir)
        log_directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=str(log_directory / "stocktaking_ai.log"),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _CONFIGURED_LOGGERS.add(name)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Retrieves an already-configured logger, or a basic default one.

    This is a lightweight convenience accessor for modules that want a
    logger without repeating full configuration parameters. Prefer calling
    `setup_logger` once with configuration values at application startup.

    Args:
        name: Logger name, typically the calling module's `__name__`.

    Returns:
        A `logging.Logger` instance.
    """
    if name in _CONFIGURED_LOGGERS:
        return logging.getLogger(name)
    return setup_logger(name)
