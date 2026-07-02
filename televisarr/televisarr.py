"""
Main application logic for Televisarr.

Orchestrates the cleanup process:
1. Connect to Plex and Sonarr
2. Get watch history from Plex
3. Evaluate seasons and series against deletion rules
4. Manage "TV Leaving Soon" collection
5. Perform deletions after grace period
"""

import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set, Tuple

from televisarr import logger
from televisarr.schema import TelevisarrConfig, LibraryConfig
from televisarr.modules.plex import PlexMediaServer
from televisarr.modules.sonarr import DSonarr
from televisarr.state import StateManager


class Televisarr:
    """Main application class for Televisarr."""

    def __init__(self, config: TelevisarrConfig):
        """
        Initialize Televisarr.

        Args:
            config: Validated Televisarr configuration
        """
        self.config = config
        self.is_dry_run = config.dry_run
        self.state_manager = StateManager()

        # Initialize connections
        self.plex = PlexMediaServer(
            config.plex.url,
            config.plex.token,
            ssl_verify=False
        )

        self.sonarr = DSonarr(
            config.sonarr.name,
            config.sonarr.url,
            config.sonarr.api_key
        )

        # Statistics tracking
        self.seasons_deleted = 0
        self.series_deleted = 0
        self.seasons_tagged = 0
        self.series_tagged = 0
        self.seasons_saved = 0
        self.series_saved = 0
        self.libraries_processed = 0
        self.libraries_failed = 0

    def has_fatal_errors(self) -> bool:
        """Return True if all libraries failed due to configuration errors."""
        total_libraries = self.libraries_processed + self.libraries_failed
        return total_libraries > 0 and self.libraries_failed == total_libraries

    def run(self) -> None:
        """
        Run the main cleanup process for all configured libraries.
        """
        logger.info("=" * 60)
        logger.info("Televisarr starting...")
        if self.is_dry_run:
            logger.info("[DRY-RUN MODE] No changes will be made")
        logger.info("=" * 60)

        for library_config in self.config.libraries:
            try:
                self._process_library(library_config)
            except Exception as e:
                logger.error(f"Failed to process library '{library_config.name}': {e}")
                self.libraries_failed += 1

        self._log_summary()

    def _process_library(self, library_config: LibraryConfig) -> None:
        """
        Process a single library.

        Args:
            library_config: Library configuration
        """
        library_name = library_config.name
        logger.info(f"Processing library: {library_name}")
        start_time = time.time()

        try:
            # Get Plex library
            plex_library = self.plex.get_library(library_name)
            library_section_id = self.plex.get_library_section_id(library_name)

            # Get watch history
            watch_history = self.plex.get_watch_history(library_section_id)
            logger.debug(f"Got {len(watch_history)} watch history entries")

            # Get all series from Sonarr
            all_series = self.sonarr.get_series()
            logger.info(f"Found {len(all_series)} series in Sonarr")

            # Process each series
            for series in all_series:
                self._process_series(library_config, plex_library, series, watch_history)

            # Clean up stale state entries
            active_series_ids = {s["id"] for s in all_series}
            active_seasons = self._get_active_seasons(all_series)
            self.state_manager.cleanup_stale_entries(
                library_name,
                active_series_ids,
                active_seasons
            )

            self.libraries_processed += 1
            duration = time.time() - start_time
            logger.info(f"Library '{library_name}' completed in {logger.format_duration(duration)}")

        except Exception as e:
            logger.error(f"Error processing library '{library_name}': {e}")
            self.libraries_failed += 1
            raise

    def _get_active_seasons(self, all_series: List[Dict]) -> Dict[int, Set[int]]:
        """
        Get active season numbers for all series.

        Args:
            all_series: List of series data from Sonarr

        Returns:
            Dict mapping series_id -> set of season numbers
        """
        active_seasons = {}
        for series in all_series:
            series_id = series["id"]
            seasons = set()
            for season in series.get("seasons", []):
                season_num = season.get("seasonNumber")
                if season_num is not None:
                    seasons.add(season_num)
            active_seasons[series_id] = seasons
        return active_seasons

    def _process_series(
        self,
        library_config: LibraryConfig,
        plex_library: Any,
        series: Dict[str, Any],
        watch_history: Dict[str, Dict]
    ) -> None:
        """
        Process a single series.

        Args:
            library_config: Library configuration
            plex_library: Plex library section
            series: Series data from Sonarr
            watch_history: Plex watch history
        """
        series_id = series["id"]
        series_title = series.get("title", "Unknown")
        series_status = series.get("status", "").lower()
        series_year = series.get("year")

        logger.debug(f"Processing series: {series_title} (ID: {series_id}, Status: {series_status})")

        # Get show from Plex - store the rating key for later use
        show = self.plex.find_show(plex_library, series_title, series_year, series.get("tvdbId"))
        if not show:
            logger.debug(f"Series '{series_title}' not found in Plex, skipping")
            return

        # Get all seasons for this series
        seasons = series.get("seasons", [])
        season_numbers = [s["seasonNumber"] for s in seasons if s.get("seasonNumber") is not None]

        if not season_numbers:
            logger.debug(f"No seasons found for series '{series_title}'")
            return

        # Check if series is eligible for series-level deletion
        series_eligible_for_deletion = self._check_series_deletion_eligibility(
            library_config,
            series,
            season_numbers,
            watch_history,
            show,
            plex_library
        )

        # Process each season
        for season_number in season_numbers:
            self._process_season(
                library_config,
                series,
                season_number,
                watch_history,
                show,
                plex_library,
                series_eligible_for_deletion
            )

        # If series is eligible for deletion, tag/delete it
        if series_eligible_for_deletion:
            self._handle_series_deletion(library_config, series, show, plex_library)

    def _process_season(
        self,
        library_config: LibraryConfig,
        series: Dict[str, Any],
        season_number: int,
        watch_history: Dict[str, Dict],
        show: Any,
        plex_library: Any,
        series_eligible_for_deletion: bool
    ) -> None:
        """
        Process a single season.

        Args:
            library_config: Library configuration
            series: Series data from Sonarr
            season_number: Season number to process
            watch_history: Plex watch history
            show: Plex show item
            plex_library: Plex library section
            series_eligible_for_deletion: Whether the series is eligible for deletion
        """
        series_id = series["id"]
        series_title = series.get("title", "Unknown")

        # Get season watch status from Plex - using plex_library directly
        season_watch_status = self.plex.get_show_season_watch_status(
            plex_library=plex_library,
            show_title=series_title,
            season_number=season_number,
            year=series.get("year"),
            tvdb_id=series.get("tvdbId")
        )

        total_episodes = season_watch_status["total_episodes"]
        if total_episodes == 0:
            logger.debug(f"Season {season_number} has no episodes, skipping")
            return

        # Check if season is eligible for deletion
        is_eligible = self._check_season_deletion_eligibility(
            library_config,
            season_watch_status,
            season_number,
            series_id
        )

        if is_eligible:
            self._handle_season_deletion(library_config, series, season_number, show, plex_library)
        elif self.state_manager.is_item_in_leaving_soon(
            library_config.name, series_id, season_number
        ):
            # Season was previously tagged but no longer eligible - save it
            self._save_season(library_config, series, season_number, show, plex_library)

    def _check_season_deletion_eligibility(
        self,
        library_config: LibraryConfig,
        season_watch_status: Dict[str, Any],
        season_number: int,
        series_id: int
    ) -> bool:
        """
        Check if a season is eligible for deletion.

        Rules:
        1. All episodes are fully watched (fully_watched rule) - with optional delay
        2. OR No episodes have been watched in X days (no_activity rule)
        3. OR Partially watched after X days (partially_watched rule, optional)

        Args:
            library_config: Library configuration
            season_watch_status: Season watch status from Plex
            season_number: Season number
            series_id: Sonarr series ID

        Returns:
            True if eligible for deletion, False otherwise
        """
        # Check if season is currently protected
        if self.state_manager.is_season_protected(library_config.name, series_id, season_number):
            logger.debug(f"Season {season_number} is protected, skipping")
            return False

        total_episodes = season_watch_status["total_episodes"]
        watched_episodes = season_watch_status["watched_episodes"]
        all_watched = season_watch_status["all_watched"]
        last_watched = season_watch_status["last_watched"]
        no_activity = season_watch_status["no_activity"]

        # Rule 1: Fully watched (with optional delay)
        fully_watched_config = library_config.season.fully_watched
        if fully_watched_config.get("enabled", True):
            if all_watched:
                # Check watch_users rule
                watch_users = fully_watched_config.get("watch_users", "any")
                if self._check_watch_users(watch_users, watched_episodes, total_episodes):
                    # Check if delay has passed
                    delay_days = fully_watched_config.get("days", 0)
                    if delay_days == 0:
                        logger.debug(f"Season {season_number} is fully watched, eligible for deletion")
                        return True
                    elif last_watched:
                        days_since = (datetime.now() - last_watched).days
                        if days_since >= delay_days:
                            logger.debug(f"Season {season_number} fully watched {days_since} days ago (delay: {delay_days}), eligible")
                            return True
                        else:
                            days_remaining = delay_days - days_since
                            logger.debug(f"Season {season_number} fully watched, waiting {days_remaining} more days")
                            return False
                    else:
                        # Shouldn't happen (all_watched implies last_watched exists)
                        logger.debug(f"Season {season_number} is fully watched but no last_watched date, delaying")
                        return False

        # Rule 2: No activity
        no_activity_config = library_config.season.no_activity
        if no_activity_config.get("enabled", False):
            days = no_activity_config.get("days", 180)
            if no_activity:
                logger.debug(f"Season {season_number} has no watch activity, eligible for deletion")
                return True

        # Rule 3: Partially watched (optional)
        partially_watched_config = library_config.season.partially_watched
        if partially_watched_config.get("enabled", False):
            days = partially_watched_config.get("days", 365)
            if not all_watched and watched_episodes > 0:
                # Check if last watch was more than X days ago
                if last_watched:
                    days_since = (datetime.now() - last_watched).days
                    if days_since >= days:
                        logger.debug(f"Season {season_number} partially watched, last watch {days_since} days ago, eligible")
                        return True

        return False

    def _check_watch_users(self, watch_users: Any, watched: int, total: int) -> bool:
        """
        Check if the watch_users rule is satisfied.

        Args:
            watch_users: 'any', 'all', or list of usernames
            watched: Number of watched episodes
            total: Total number of episodes

        Returns:
            True if the rule is satisfied
        """
        if watch_users == "any":
            return watched == total
        elif watch_users == "all":
            # "all" means all watched episodes must be by the same user? or all users?
            # For simplicity, we treat "all" as "all episodes are watched"
            # In a multi-user setup, we'd need more complex logic
            return watched == total
        elif isinstance(watch_users, list):
            # For specific users, we'd need to check per-user watch history
            # This is a simplified version - assumes if episodes are watched, they're by the specified users
            return watched == total
        return watched == total

    def _handle_season_deletion(
        self,
        library_config: LibraryConfig,
        series: Dict[str, Any],
        season_number: int,
        show: Any,
        plex_library: Any
    ) -> None:
        """
        Handle season deletion (tag or delete).

        Args:
            library_config: Library configuration
            series: Series data from Sonarr
            season_number: Season number
            show: Plex show item
            plex_library: Plex library section
        """
        library_name = library_config.name
        series_id = series["id"]
        series_title = series.get("title", "Unknown")

        # Check if season is already tagged
        if self.state_manager.is_item_in_leaving_soon(library_name, series_id, season_number):
            # Season is tagged - check if grace period has passed
            tagged_at = self.state_manager.get_season_tagged_at(library_name, series_id, season_number)
            if tagged_at:
                days_since_tagged = (datetime.now() - tagged_at).days
                grace_period = library_config.grace_period

                if days_since_tagged >= grace_period:
                    # Delete the season
                    logger.info(f"Season {season_number} of '{series_title}' has been in TV Leaving Soon for {days_since_tagged} days (grace period: {grace_period})")

                    if self.is_dry_run:
                        logger.info(f"[DRY-RUN] Would delete season {season_number} of '{series_title}'")
                    else:
                        success = self.sonarr.delete_season(series_id, season_number, delete_files=True)
                        if success:
                            self.seasons_deleted += 1
                            self.state_manager.untag_season(library_name, series_id, season_number)
                            logger.info(f"Deleted season {season_number} of '{series_title}'")
                        else:
                            logger.error(f"Failed to delete season {season_number} of '{series_title}'")
                    return
                else:
                    # Still in grace period
                    days_remaining = grace_period - days_since_tagged
                    logger.debug(f"Season {season_number} of '{series_title}' in grace period ({days_remaining} days remaining)")
                    return

        # Season is not tagged - tag it
        logger.info(f"Season {season_number} of '{series_title}' is eligible for deletion")

        if self.is_dry_run:
            logger.info(f"[DRY-RUN] Would tag season {season_number} of '{series_title}' for deletion")
        else:
            # Add to state
            self.state_manager.tag_season(library_name, series_id, season_number)
            self.seasons_tagged += 1

            # Add to Plex collection
            self._add_to_leaving_soon_collection(library_config, plex_library, show, season_number)
            logger.info(f"Tagged season {season_number} of '{series_title}' for deletion")

    def _save_season(
        self,
        library_config: LibraryConfig,
        series: Dict[str, Any],
        season_number: int,
        show: Any,
        plex_library: Any
    ) -> None:
        """
        Save a season from deletion (remove from TV Leaving Soon).

        Args:
            library_config: Library configuration
            series: Series data from Sonarr
            season_number: Season number
            show: Plex show item
            plex_library: Plex library section
        """
        library_name = library_config.name
        series_id = series["id"]
        series_title = series.get("title", "Unknown")

        logger.info(f"Season {season_number} of '{series_title}' is no longer eligible for deletion - saving")

        if self.is_dry_run:
            logger.info(f"[DRY-RUN] Would save season {season_number} of '{series_title}'")
        else:
            # Remove from state
            self.state_manager.untag_season(library_name, series_id, season_number)

            # Protect it for a period
            if library_config.protection.enabled:
                self.state_manager.protect_season(
                    library_name,
                    series_id,
                    season_number,
                    library_config.protection.save_days
                )

            # Remove from Plex collection
            self._remove_from_leaving_soon_collection(library_config, plex_library, show, season_number)
            self.seasons_saved += 1
            logger.info(f"Saved season {season_number} of '{series_title}'")

    def _check_series_deletion_eligibility(
        self,
        library_config: LibraryConfig,
        series: Dict[str, Any],
        season_numbers: List[int],
        watch_history: Dict[str, Dict],
        show: Any,
        plex_library: Any
    ) -> bool:
        """
        Check if a series is eligible for deletion.

        Rules:
        1. All seasons are fully watched
        2. Series status is 'ended' or 'cancelled' (if require_ended is True)

        Args:
            library_config: Library configuration
            series: Series data from Sonarr
            season_numbers: List of season numbers
            watch_history: Plex watch history
            show: Plex show item
            plex_library: Plex library section

        Returns:
            True if eligible for deletion, False otherwise
        """
        if not library_config.series.enabled:
            return False

        series_title = series.get("title", "Unknown")
        series_status = series.get("status", "").lower()

        # Check if series has ended
        if library_config.series.require_ended:
            if series_status not in ["ended", "cancelled"]:
                logger.debug(f"Series '{series_title}' is still continuing (status: {series_status}), skipping series deletion")
                return False

        # Check if all seasons are fully watched
        for season_num in season_numbers:
            season_watch_status = self.plex.get_show_season_watch_status(
                plex_library=plex_library,
                show_title=series_title,
                season_number=season_num,
                year=series.get("year"),
                tvdb_id=series.get("tvdbId")
            )

            if not season_watch_status["all_watched"]:
                logger.debug(f"Season {season_num} of '{series_title}' is not fully watched, skipping series deletion")
                return False

        logger.debug(f"Series '{series_title}' is fully watched and ended, eligible for deletion")
        return True

    def _handle_series_deletion(
        self,
        library_config: LibraryConfig,
        series: Dict[str, Any],
        show: Any,
        plex_library: Any
    ) -> None:
        """
        Handle series deletion (tag or delete).

        Args:
            library_config: Library configuration
            series: Series data from Sonarr
            show: Plex show item
            plex_library: Plex library section
        """
        library_name = library_config.name
        series_id = series["id"]
        series_title = series.get("title", "Unknown")

        # Check if series is already tagged
        if self.state_manager.is_item_in_leaving_soon(library_name, series_id):
            # Series is tagged - check if grace period has passed
            tagged_at = self.state_manager.get_series_tagged_at(library_name, series_id)
            if tagged_at:
                days_since_tagged = (datetime.now() - tagged_at).days
                grace_period = library_config.series.grace_period

                if days_since_tagged >= grace_period:
                    # Delete the series
                    logger.info(f"Series '{series_title}' has been in TV Leaving Soon for {days_since_tagged} days (grace period: {grace_period})")

                    if self.is_dry_run:
                        logger.info(f"[DRY-RUN] Would delete series '{series_title}'")
                    else:
                        # Get all seasons and untag them
                        seasons = self.sonarr.get_seasons(series_id)
                        for season in seasons:
                            season_num = season.get("seasonNumber")
                            if season_num is not None:
                                self.state_manager.untag_season(library_name, series_id, season_num)

                        # Delete the series
                        success = self.sonarr.delete_series(series_id, delete_files=True, add_exclusion=False)
                        if success:
                            self.series_deleted += 1
                            self.state_manager.untag_series(library_name, series_id)
                            logger.info(f"Deleted series '{series_title}'")
                        else:
                            logger.error(f"Failed to delete series '{series_title}'")
                    return
                else:
                    days_remaining = grace_period - days_since_tagged
                    logger.debug(f"Series '{series_title}' in grace period ({days_remaining} days remaining)")
                    return

        # Series is not tagged - tag it
        logger.info(f"Series '{series_title}' is eligible for deletion")

        if self.is_dry_run:
            logger.info(f"[DRY-RUN] Would tag series '{series_title}' for deletion")
        else:
            # Add to state
            self.state_manager.tag_series(library_name, series_id)
            self.series_tagged += 1

            # Add to Plex collection (all seasons)
            seasons = self.sonarr.get_seasons(series_id)
            for season in seasons:
                season_num = season.get("seasonNumber")
                if season_num is not None:
                    self._add_to_leaving_soon_collection(library_config, plex_library, show, season_num)

            logger.info(f"Tagged series '{series_title}' for deletion")

    def _add_to_leaving_soon_collection(
        self,
        library_config: LibraryConfig,
        plex_library: Any,
        show: Any,
        season_number: int
    ) -> None:
        """
        Add a season to the TV Leaving Soon collection.

        Args:
            library_config: Library configuration
            plex_library: Plex library section
            show: Plex show item
            season_number: Season number to add
        """
        collection_name = library_config.leaving_soon.collection_name
        description = library_config.leaving_soon.description

        try:
            episodes = show.episodes()
            season_episodes = [ep for ep in episodes if ep.seasonNumber == season_number]
            if not season_episodes:
                logger.debug(f"No episodes found for season {season_number}")
                return

            collection = self.plex.get_or_create_collection(
                plex_library,
                collection_name,
                description=description
            )

            if collection:
                self.plex.set_collection_items(collection, season_episodes)
                self.plex.set_collection_visibility(collection, home=True, shared=True)

        except Exception as e:
            logger.warning(f"Failed to add season {season_number} to collection: {e}")

    def _remove_from_leaving_soon_collection(
        self,
        library_config: LibraryConfig,
        plex_library: Any,
        show: Any,
        season_number: int
    ) -> None:
        """
        Remove a season from the TV Leaving Soon collection.

        Args:
            library_config: Library configuration
            plex_library: Plex library section
            show: Plex show item
            season_number: Season number to remove
        """
        collection_name = library_config.leaving_soon.collection_name

        try:
            collection = plex_library.collection(collection_name)
            episodes = show.episodes()
            season_episodes = [ep for ep in episodes if ep.seasonNumber == season_number]

            if collection and season_episodes:
                current_items = collection.items()
                items_to_remove = [item for item in current_items if item in season_episodes]
                if items_to_remove:
                    collection.removeItems(items_to_remove)
                    logger.debug(f"Removed {len(items_to_remove)} episodes from collection")

        except Exception as e:
            logger.debug(f"Failed to remove season {season_number} from collection: {e}")

    def _log_summary(self) -> None:
        """Log a summary of the run."""
        separator = "=" * 60

        logger.info(separator)
        logger.info("TELEVISARR RUN SUMMARY")
        logger.info(separator)

        if self.is_dry_run:
            logger.info("[DRY-RUN MODE] No changes were made")

        logger.info(f"Libraries processed: {self.libraries_processed}")
        if self.libraries_failed > 0:
            logger.info(f"Libraries failed: {self.libraries_failed}")

        logger.info("-" * 40)
        logger.info(f"Seasons tagged for deletion:  {self.seasons_tagged}")
        logger.info(f"Seasons deleted:              {self.seasons_deleted}")
        logger.info(f"Seasons saved:                {self.seasons_saved}")
        logger.info(f"Series tagged for deletion:   {self.series_tagged}")
        logger.info(f"Series deleted:               {self.series_deleted}")
        logger.info(f"Series saved:                 {self.series_saved}")

        logger.info(separator)