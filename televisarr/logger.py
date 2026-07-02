import logging
import os
import sys
from logging import handlers

# These settings are for file logging only
FILENAME = "televisarr.log"
MAX_SIZE = 5000000  # 5 MB
MAX_FILES = 5

logging.basicConfig()

# Televisarr logger
logger = logging.getLogger("televisarr")


class LogLevelFilter(logging.Filter):
    def __init__(self, max_level):
        super(LogLevelFilter, self).__init__()
        self.max_level = max_level

    def filter(self, record):
        return record.levelno <= self.max_level


def init_logger(console=False, log_dir=False, verbose=False):
    """
    Setup logging for Televisarr.
    
    Args:
        console: Whether to log to console
        log_dir: Directory for log files
        verbose: Whether to enable debug logging
    """
    remove_old_handlers()
    configure_logger(verbose)

    if log_dir:
        setup_file_logger(log_dir)

    if console:
        setup_console_logger()


def remove_old_handlers():
    log_handlers = logger.handlers[:]
    for handler in log_handlers:
        if isinstance(handler, handlers.RotatingFileHandler):
            handler.close()
        elif isinstance(handler, logging.StreamHandler):
            handler.flush()
        logger.removeHandler(handler)


def configure_logger(verbose):
    logger.propagate = False
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


def setup_file_logger(log_dir):
    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)-7s :: %(filename)s :: %(name)s : %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    filename = os.path.join(log_dir, FILENAME)
    file_handler = handlers.RotatingFileHandler(
        filename, maxBytes=MAX_SIZE, backupCount=MAX_FILES, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)


def setup_console_logger():
    console_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s :: %(filename)s :: %(name)s : %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(console_formatter)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.addFilter(LogLevelFilter(logging.INFO))

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(console_formatter)
    stderr_handler.setLevel(logging.WARNING)

    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)


# Expose logger methods
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