"""
Logging configuration for Televisarr.
"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# These settings are for file logging only
FILENAME = "televisarr.log"
LOG_DIR = Path("/config/logs")
MAX_DAYS = 5  # Keep 5 days of logs (configurable)


def init_logger(console=False, log_dir=None, verbose=False, max_days=None):
    """
    Setup logging for Televisarr with daily rotation.
    
    Args:
        console: Whether to log to console
        log_dir: Directory for log files
        verbose: Whether to enable debug logging
        max_days: Number of days to keep (default: 5)
    """
    # Use provided log_dir or default
    log_path = Path(log_dir) / FILENAME if log_dir else LOG_DIR / FILENAME
    max_days = max_days or MAX_DAYS
    
    # Create log directory if it doesn't exist
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Get the root logger
    logger = logging.getLogger("televisarr")
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Set log level
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    
    # File handler with daily rotation
    file_handler = TimedRotatingFileHandler(
        str(log_path),
        when="midnight",      # rotate at midnight
        interval=1,           # every day
        backupCount=max_days, # keep X days
        encoding="utf-8"
    )
    file_handler.suffix = "%Y-%m-%d"  # televisarr.log.2026-07-04 format
    
    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)-7s :: %(filename)s :: %(name)s : %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    
    # Console handler (for Docker logs)
    if console:
        console_formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s :: %(filename)s :: %(name)s : %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.DEBUG)
        logger.addHandler(console_handler)
    
    logger.info(f"Logging to {log_path} with {max_days} days retention")


# The logger object that other modules will import
logger = logging.getLogger("televisarr")

# Convenience methods (these work because logger is the same instance
# that init_logger() configures)
info = logger.info
warn = logger.warning
error = logger.error
debug = logger.debug
warning = logger.warning
exception = logger.exception


def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration string."""
    if seconds >= 3600:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}h {minutes}m {secs:.1f}s"
    elif seconds >= 60:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.1f}s"
    return f"{seconds:.1f}s"