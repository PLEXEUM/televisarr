"""
Entry point for Televisarr.

Handles:
- Command-line argument parsing
- Configuration loading and validation
- Running in single-run or scheduler mode
- Instance locking (prevent duplicate runs)
"""

import argparse
import atexit
import locale
import os
import signal
import sys
import time
from pathlib import Path

from televisarr import logger, __version__
from televisarr.config import load_config, validate_connections, hang_on_error
from televisarr.schema import TelevisarrConfig

# Lock file for single instance detection
LOCK_FILE = "/config/.televisarr.lock"
_lock_file_handle = None


def cleanup_stale_lock():
    """
    Remove stale lock file if it exists.
    
    A lock file is considered stale if:
    1. It's older than 10 minutes (likely from a crashed process)
    2. The PID in the file no longer exists (on Unix)
    """
    logger.debug(f"Checking for stale lock file at: {LOCK_FILE}")
    
    if not os.path.exists(LOCK_FILE):
        logger.debug("No lock file found - clean start")
        return

    try:
        # Get file stats
        mtime = os.path.getmtime(LOCK_FILE)
        age_seconds = time.time() - mtime
        age_minutes = age_seconds / 60
        
        logger.debug(f"Lock file exists - age: {age_minutes:.1f} minutes ({age_seconds:.0f} seconds)")
        
        # Read PID if possible
        pid_content = None
        try:
            with open(LOCK_FILE, "r") as f:
                pid_content = f.read().strip()
            logger.debug(f"Lock file contains PID: {pid_content}")
        except Exception as e:
            logger.debug(f"Could not read PID from lock file: {e}")

        # Check 1: File age - if older than 10 minutes, it's stale
        if age_seconds > 600:  # 10 minutes
            logger.warning(
                f"Removing stale lock file (modified {age_minutes:.1f} minutes ago, > 10 minutes)"
            )
            os.remove(LOCK_FILE)
            logger.debug("Stale lock file removed (age check)")
            return

        # Check 2: On Unix, check if the PID still exists
        if sys.platform != "win32" and pid_content:
            try:
                old_pid = int(pid_content)
                logger.debug(f"Checking if PID {old_pid} is still running...")
                
                # Check if process exists (signal 0 doesn't kill, just checks)
                try:
                    os.kill(old_pid, 0)
                    logger.debug(f"Lock file valid: PID {old_pid} is running")
                except OSError as e:
                    logger.warning(
                        f"Removing stale lock file (PID {old_pid} no longer exists, error: {e})"
                    )
                    os.remove(LOCK_FILE)
                    logger.debug("Stale lock file removed (PID check)")
                    return
            except (ValueError, FileNotFoundError, OSError) as e:
                logger.warning(f"Removing invalid lock file (invalid PID content): {e}")
                os.remove(LOCK_FILE)
                logger.debug("Invalid lock file removed")
                return

        # Check 3: On Windows, we rely on age check only
        if sys.platform == "win32":
            logger.debug(f"Lock file is {age_minutes:.1f} minutes old - keeping (under 10 minute threshold)")
            logger.debug("On Windows, locks are only cleaned up by age (> 10 minutes)")
            
        # If we get here, lock file is considered valid
        logger.debug("Lock file is valid - keeping it")

    except OSError as e:
        logger.debug(f"Could not check lock file: {e}")
    except Exception as e:
        logger.debug(f"Unexpected error checking lock file: {e}")


def acquire_instance_lock() -> bool:
    """
    Try to acquire an exclusive lock to ensure only one instance runs.

    Returns:
        bool: True if lock acquired, False if another instance is running.
    """
    global _lock_file_handle

    # On Windows, use a simpler file-based lock since fcntl isn't available
    if sys.platform == "win32":
        try:
            # Clean up stale lock first
            cleanup_stale_lock()
            
            # Try to create the lock file exclusively
            if os.path.exists(LOCK_FILE):
                return False
            
            with open(LOCK_FILE, "w") as f:
                f.write(str(os.getpid()))
            
            atexit.register(release_instance_lock)
            return True
        except Exception as e:
            logger.error(f"Failed to acquire lock on Windows: {e}")
            return False

    # Unix/Linux: use fcntl flock
    try:
        import fcntl
        
        # Clean up stale lock first
        cleanup_stale_lock()

        _lock_file_handle = open(LOCK_FILE, "w")
        fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()

        # Register cleanup on exit
        atexit.register(release_instance_lock)
        return True
    except (IOError, OSError):
        # Lock is held by another process
        return False
    except ImportError:
        # fcntl not available (non-Unix)
        return True


def release_instance_lock() -> None:
    """Release the instance lock."""
    global _lock_file_handle

    if _lock_file_handle:
        try:
            if sys.platform != "win32":
                import fcntl
                fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_UN)
            _lock_file_handle.close()
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass
        _lock_file_handle = None


def get_file_contents(file_path: str) -> str:
    """Read a file and return its contents stripped of whitespace."""
    try:
        with open(file_path, "r") as file:
            return file.read().strip()
    except FileNotFoundError:
        return "unknown"
    except IOError as e:
        print(f"Error reading file {file_path}: {e}")
        return "unknown"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Televisarr - Intelligent TV show cleanup for Plex using Sonarr"
    )
    parser.add_argument(
        "--config",
        "-c",
        default="/config/televisarr.yaml",
        help="Path to the config file (default: /config/televisarr.yaml)",
    )
    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="Force scheduler mode (overrides config setting)",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Force single run mode (overrides scheduler config)",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"Televisarr {__version__}",
    )

    return parser.parse_args()


def init_logging(log_level: str = "INFO") -> None:
    """
    Initialize logging.

    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    verbose = log_level.upper() == "DEBUG"
    logger.init_logger(
        console=True,
        log_dir="/config/logs",
        verbose=verbose,
    )


def run_televisarr(config: TelevisarrConfig) -> bool:
    """
    Run Televisarr once.

    Args:
        config: Validated configuration

    Returns:
        bool: True if successful, False if fatal errors occurred
    """
    from televisarr.televisarr import Televisarr

    try:
        televisarr = Televisarr(config)
        televisarr.run()

        if televisarr.has_fatal_errors():
            logger.error(
                "All libraries failed due to configuration errors. "
                "Please check your configuration and fix the errors above."
            )
            return False

        return True
    except Exception as e:
        logger.error(f"Televisarr run failed: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


def main() -> None:
    """Main entry point for Televisarr."""
    # Set locale for proper string formatting
    try:
        locale.setlocale(locale.LC_ALL, "")
    except Exception:
        pass

    # Parse arguments
    args = parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
        log_level = config.log_level if hasattr(config, 'log_level') else "INFO"
    except Exception as e:
        print(f"Failed to load configuration: {e}")
        sys.exit(1)

    # Initialize logging
    init_logging(log_level)

    # Log version information
    logger.info(f"Log level set to {log_level}")

    # Determine run mode
    scheduler_enabled = False
    if config.scheduler:
        scheduler_enabled = config.scheduler.enabled

    # CLI flags override config
    if args.run_once:
        scheduler_enabled = False
    elif args.scheduler:
        scheduler_enabled = True

    # Check for another running instance
    if not acquire_instance_lock():
        logger.warning("=" * 60)
        logger.warning("Another televisarr instance is already running!")
        logger.warning("=" * 60)
        if scheduler_enabled:
            logger.warning(
                "The built-in scheduler is enabled by default. "
                "If you're using an external scheduler (Ofelia, cron), either:"
            )
            logger.warning("")
            logger.warning("  1. Remove Ofelia and use the built-in scheduler (recommended)")
            logger.warning("")
            logger.warning("  2. Or disable the built-in scheduler in televisarr.yaml:")
            logger.warning("     scheduler:")
            logger.warning("       enabled: false")
        else:
            logger.warning(
                "A previous run may still be in progress. "
                "Wait for it to complete or check for stuck processes."
            )
        logger.warning("Exiting to prevent duplicate runs.")
        sys.exit(1)

    # Validate connections
    if not validate_connections(config):
        hang_on_error(
            "Failed to validate connections to Plex and/or Sonarr. "
            "Please check your configuration and ensure services are running."
        )

    if scheduler_enabled:
        # Run in scheduler mode (long-lived process)
        from televisarr.scheduler import TelevisarrScheduler

        logger.info("Starting in scheduler mode")
        scheduler = TelevisarrScheduler(config)
        scheduler.start()  # Blocks until shutdown
    else:
        # Run once and exit
        logger.info("Running in single-run mode")
        success = run_televisarr(config)

        if not success:
            hang_on_error(
                "Televisarr run failed due to configuration errors. "
                "Please check your configuration and fix the errors above."
            )

        sys.exit(0)


if __name__ == "__main__":
    main()