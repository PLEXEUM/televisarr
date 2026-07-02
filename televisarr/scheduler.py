"""
Built-in scheduler for Televisarr.

Provides an optional native scheduling solution as an alternative to external
schedulers like Ofelia or system cron.
"""

import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from televisarr import logger
from televisarr.config import hang_on_error
from televisarr.schema import TelevisarrConfig


# Schedule presets mapping to cron expressions
SCHEDULE_PRESETS = {
    "hourly": "0 * * * *",      # Every hour at minute 0
    "daily": "0 3 * * *",       # Daily at 3 AM
    "weekly": "0 3 * * 0",      # Sunday at 3 AM
    "monthly": "0 3 1 * *",     # First day of month at 3 AM
}


class TelevisarrScheduler:
    """
    Scheduler for running Televisarr on a configurable schedule.

    Supports both cron expressions and preset schedules (hourly, daily, weekly, monthly).
    """

    def __init__(self, config: TelevisarrConfig):
        """
        Initialize the scheduler.

        Args:
            config: TelevisarrConfig object
        """
        self.config = config
        self.scheduler_config = config.scheduler
        self.scheduler = BlockingScheduler()
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Set up graceful shutdown handlers."""
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info("Received shutdown signal, stopping scheduler...")
        self.scheduler.shutdown(wait=False)
        sys.exit(0)

    def _parse_schedule(self, schedule: str) -> CronTrigger:
        """
        Parse schedule string into a CronTrigger.

        Args:
            schedule: Either a preset name (hourly, daily, weekly, monthly)
                     or a cron expression (e.g., "0 3 * * 0")

        Returns:
            CronTrigger configured for the schedule

        Raises:
            ValueError: If the schedule format is invalid
        """
        # Check if it's a preset
        if schedule.lower() in SCHEDULE_PRESETS:
            cron_expr = SCHEDULE_PRESETS[schedule.lower()]
            logger.info(f"Using schedule preset '{schedule}' ({cron_expr})")
        else:
            cron_expr = schedule
            logger.info(f"Using custom cron schedule: {cron_expr}")

        # Parse cron expression
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(
                f"Invalid cron expression '{cron_expr}'. "
                "Expected 5 fields: minute hour day month day_of_week"
            )

        minute, hour, day, month, day_of_week = parts
        timezone = self.scheduler_config.timezone if self.scheduler_config else "UTC"

        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=timezone,
        )

    def _run_televisarr(self) -> bool:
        """
        Execute Televisarr cleanup job.

        Returns:
            bool: True if run completed successfully, False if there were fatal errors.
        """
        from televisarr.televisarr import Televisarr

        logger.info("=" * 60)
        logger.info(f"Scheduled run starting at {datetime.now().isoformat()}")
        logger.info("=" * 60)

        try:
            televisarr = Televisarr(self.config)
            if televisarr.has_fatal_errors():
                logger.error(
                    "All libraries failed due to configuration errors. "
                    "Please check your configuration and fix the errors above."
                )
                return False
            if self.config.dry_run:
                logger.info("[DRY-RUN] Scheduled run completed successfully (no changes were made)")
            else:
                logger.info("Scheduled run completed successfully")
            return True
        except Exception as e:
            logger.error(f"Scheduled run failed: {e}")
            # Don't re-raise - we want the scheduler to continue for transient errors
            return True  # Not a config error, allow retries

    def start(self):
        """
        Start the scheduler.

        This method blocks until the scheduler is stopped.
        """
        if not self.scheduler_config:
            logger.error("Scheduler configuration is missing")
            hang_on_error("Scheduler enabled but no scheduler configuration found")

        schedule = self.scheduler_config.schedule
        run_on_startup = self.scheduler_config.run_on_startup

        try:
            trigger = self._parse_schedule(schedule)
        except ValueError as e:
            hang_on_error(f"Invalid schedule configuration: {e}")

        # Add the job
        self.scheduler.add_job(
            self._run_televisarr,
            trigger,
            id="televisarr_cleanup",
            name="Televisarr Media Cleanup",
            replace_existing=True,
        )

        logger.info("Televisarr scheduler started")
        logger.info(f"Schedule: {schedule}")
        logger.info(f"Timezone: {self.scheduler_config.timezone if self.scheduler_config else 'UTC'}")

        # Run immediately if configured
        if run_on_startup:
            logger.info("run_on_startup enabled, executing initial run...")
            success = self._run_televisarr()
            if not success:
                hang_on_error(
                    "Initial run failed due to configuration errors. "
                    "Scheduler will not start until configuration is fixed."
                )

        # Start the scheduler (blocks)
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped")