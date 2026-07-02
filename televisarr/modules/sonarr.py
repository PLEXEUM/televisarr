"""
Sonarr API integration for Televisarr.

Provides Sonarr API functionality for:
- Getting series, seasons, and episodes
- Deleting seasons and series
- Unmonitoring seasons
- Checking series status (ended, continuing, cancelled)
"""

from typing import Optional, List, Dict, Any
from pyarr.sonarr import SonarrAPI

from televisarr import logger


class DSonarr:
    """Sonarr instance wrapper with Televisarr-specific methods."""

    def __init__(self, sonarr_name: str, sonarr_url: str, sonarr_api_key: str):
        """
        Initialize Sonarr connection.

        Args:
            sonarr_name: Name identifier for this instance
            sonarr_url: Sonarr server URL
            sonarr_api_key: Sonarr API key
        """
        self.sonarr_name = sonarr_name
        self.sonarr_url = sonarr_url
        self.sonarr_api_key = sonarr_api_key

        self.instance = SonarrAPI(sonarr_url, sonarr_api_key)
        self._tags_cache = None
        self._quality_profiles_cache = None

    def __getattr__(self, name):
        """Delegate unknown attributes to the underlying SonarrAPI instance."""
        if hasattr(self.instance, name):
            return getattr(self.instance, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def validate_connection(self) -> bool:
        """
        Validate the Sonarr connection.

        Returns:
            True if connection is successful, False otherwise
        """
        try:
            self.instance.get_health()
            logger.debug(f"Sonarr connection validated for '{self.sonarr_name}'")
            return True
        except Exception as e:
            logger.error(f"Sonarr connection failed for '{self.sonarr_name}': {e}")
            return False

    def get_series(self) -> List[Dict[str, Any]]:
        """
        Get all series from Sonarr.

        Returns:
            List of series data dicts
        """
        try:
            return self.instance.get_series()
        except Exception as e:
            logger.error(f"Failed to get series from Sonarr: {e}")
            return []

    def get_series_by_id(self, series_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific series by ID.

        Args:
            series_id: Sonarr series ID

        Returns:
            Series data dict or None if not found
        """
        try:
            return self.instance.get_series(series_id)
        except Exception as e:
            logger.debug(f"Failed to get series {series_id}: {e}")
            return None

    def get_series_by_tvdb(self, tvdb_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a series by TVDB ID.

        Args:
            tvdb_id: TVDB ID

        Returns:
            Series data dict or None if not found
        """
        try:
            return self.instance.get_series(tvdb_id, tvdb=True)
        except Exception as e:
            logger.debug(f"Failed to get series by TVDB ID {tvdb_id}: {e}")
            return None

    def get_seasons(self, series_id: int) -> List[Dict[str, Any]]:
        """
        Get all seasons for a series.

        Args:
            series_id: Sonarr series ID

        Returns:
            List of season data dicts
        """
        series = self.get_series_by_id(series_id)
        if not series:
            return []
        return series.get("seasons", [])

    def get_season_by_number(self, series_id: int, season_number: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific season by number.

        Args:
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            Season data dict or None if not found
        """
        seasons = self.get_seasons(series_id)
        for season in seasons:
            if season.get("seasonNumber") == season_number:
                return season
        return None

    def get_episodes(self, series_id: int) -> List[Dict[str, Any]]:
        """
        Get all episodes for a series.

        Args:
            series_id: Sonarr series ID

        Returns:
            List of episode data dicts
        """
        try:
            return self.instance.get_episode(series_id, series=True)
        except Exception as e:
            logger.error(f"Failed to get episodes for series {series_id}: {e}")
            return []

    def get_episodes_by_season(self, series_id: int, season_number: int) -> List[Dict[str, Any]]:
        """
        Get all episodes for a specific season.

        Args:
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            List of episode data dicts
        """
        episodes = self.get_episodes(series_id)
        return [ep for ep in episodes if ep.get("seasonNumber") == season_number]

    def get_episode_files_by_season(self, series_id: int, season_number: int) -> List[Dict[str, Any]]:
        """
        Get all episode files for a specific season.

        Args:
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            List of episode file data dicts
        """
        episodes = self.get_episodes_by_season(series_id, season_number)
        episode_files = []
        for episode in episodes:
            if episode.get("episodeFileId"):
                try:
                    episode_file = self.instance.get_episode_file(episode["episodeFileId"])
                    if episode_file:
                        episode_files.append(episode_file)
                except Exception as e:
                    logger.debug(f"Failed to get episode file for {episode.get('id')}: {e}")
        return episode_files

    def delete_season(
        self,
        series_id: int,
        season_number: int,
        delete_files: bool = True
    ) -> bool:
        """
        Delete a season and optionally its files.

        This is the primary method for season-level deletion.

        Args:
            series_id: Sonarr series ID
            season_number: Season number to delete
            delete_files: Whether to delete the files from disk

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Deleting season {season_number} from series {series_id}")

        try:
            # Get all episodes in the season
            episodes = self.get_episodes_by_season(series_id, season_number)
            if not episodes:
                logger.warning(f"No episodes found for season {season_number}")
                return False

            # Unmonitor all episodes first
            episode_ids = [ep["id"] for ep in episodes]
            try:
                self.instance.upd_episode_monitor(episode_ids, False)
                logger.debug(f"Unmonitored {len(episode_ids)} episodes in season {season_number}")
            except Exception as e:
                logger.warning(f"Failed to unmonitor episodes: {e}")

            # Delete episode files
            if delete_files:
                for episode in episodes:
                    episode_file_id = episode.get("episodeFileId")
                    if episode_file_id and episode_file_id > 0:
                        try:
                            self.instance.del_episode_file(episode_file_id)
                            logger.debug(f"Deleted episode file {episode_file_id}")
                        except Exception as e:
                            logger.warning(f"Failed to delete episode file {episode_file_id}: {e}")

            # Get the series and update season status
            series = self.get_series_by_id(series_id)
            if series:
                # Update season to be unmonitored
                seasons = series.get("seasons", [])
                for season in seasons:
                    if season.get("seasonNumber") == season_number:
                        season["monitored"] = False
                        break

                # Update the series with the modified season
                try:
                    # We need to update the whole series to persist the season change
                    self.instance.upd_series(series)
                    logger.debug(f"Updated series {series_id} season {season_number} to unmonitored")
                except Exception as e:
                    logger.warning(f"Failed to update series with unmonitored season: {e}")

            logger.info(f"Successfully deleted season {season_number}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete season {season_number}: {e}")
            return False

    def delete_series(
        self,
        series_id: int,
        delete_files: bool = True,
        add_exclusion: bool = False
    ) -> bool:
        """
        Delete a series and optionally its files.

        This is the primary method for series-level deletion.

        Args:
            series_id: Sonarr series ID
            delete_files: Whether to delete the files from disk
            add_exclusion: Whether to add the series to the exclusion list

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Deleting series {series_id}")

        try:
            # Get series details for logging
            series = self.get_series_by_id(series_id)
            series_title = series.get("title", "Unknown") if series else "Unknown"

            # Delete the series
            self.instance.del_series(series_id, delete_files=delete_files, add_exclusion=add_exclusion)
            logger.info(f"Successfully deleted series '{series_title}'")
            return True

        except Exception as e:
            logger.error(f"Failed to delete series {series_id}: {e}")
            return False

    def get_series_status(self, series_id: int) -> Optional[str]:
        """
        Get the status of a series.

        Returns:
            'continuing', 'ended', 'cancelled', or None if not found
        """
        series = self.get_series_by_id(series_id)
        if not series:
            return None
        return series.get("status", "").lower()

    def is_series_ended(self, series_id: int) -> bool:
        """
        Check if a series has ended or been cancelled.

        Returns:
            True if series status is 'ended' or 'cancelled'
        """
        status = self.get_series_status(series_id)
        return status in ["ended", "cancelled"]

    def is_series_continuing(self, series_id: int) -> bool:
        """
        Check if a series is still continuing.

        Returns:
            True if series status is 'continuing'
        """
        status = self.get_series_status(series_id)
        return status == "continuing"

    def get_season_files(self, series_id: int, season_number: int) -> List[Dict[str, Any]]:
        """
        Get all episode files for a season.

        Args:
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            List of episode file data dicts
        """
        episodes = self.get_episodes_by_season(series_id, season_number)
        files = []
        for episode in episodes:
            if episode.get("episodeFileId"):
                try:
                    file_data = self.instance.get_episode_file(episode["episodeFileId"])
                    if file_data:
                        files.append(file_data)
                except Exception:
                    pass
        return files

    def get_season_size(self, series_id: int, season_number: int) -> int:
        """
        Get the total size of a season's episode files.

        Args:
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            Total size in bytes
        """
        files = self.get_season_files(series_id, season_number)
        total_size = 0
        for file_data in files:
            total_size += file_data.get("size", 0)
        return total_size

    def get_season_episode_count(self, series_id: int, season_number: int) -> int:
        """
        Get the total number of episodes in a season.

        Args:
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            Number of episodes
        """
        episodes = self.get_episodes_by_season(series_id, season_number)
        return len(episodes)

    def get_season_watched_status(self, series_id: int, season_number: int) -> Dict[str, Any]:
        """
        Get the watched status of a season from Sonarr's perspective.

        Note: This checks the Sonarr database, not Plex watch history.
        For Plex watch history, use PlexMediaServer instead.

        Args:
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            Dict with:
                - monitored: Whether the season is monitored
                - episode_count: Number of episodes in the season
                - downloaded_count: Number of downloaded episodes
                - fully_downloaded: True if all episodes are downloaded
        """
        season = self.get_season_by_number(series_id, season_number)
        if not season:
            return {
                "monitored": False,
                "episode_count": 0,
                "downloaded_count": 0,
                "fully_downloaded": False,
            }

        episodes = self.get_episodes_by_season(series_id, season_number)
        downloaded = [ep for ep in episodes if ep.get("episodeFileId")]
        
        return {
            "monitored": season.get("monitored", False),
            "episode_count": len(episodes),
            "downloaded_count": len(downloaded),
            "fully_downloaded": len(downloaded) == len(episodes) if episodes else False,
        }

    def get_season_path(self, series_id: int, season_number: int) -> Optional[str]:
        """
        Get the path of a season.

        Args:
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            Season path, or None if not found
        """
        series = self.get_series_by_id(series_id)
        if not series:
            return None
        
        series_path = series.get("path", "")
        # Sonarr typically uses format: /path/to/series/Season XX
        if season_number == 0:
            return f"{series_path}/Season 0"
        else:
            return f"{series_path}/Season {season_number:02d}"

    def get_tags(self) -> List[Dict[str, Any]]:
        """
        Get all tags from Sonarr.

        Returns:
            List of tag data dicts
        """
        if self._tags_cache is None:
            try:
                self._tags_cache = self.instance.get_tag()
            except Exception as e:
                logger.error(f"Failed to get tags from Sonarr: {e}")
                self._tags_cache = []
        return self._tags_cache

    def get_quality_profiles(self) -> List[Dict[str, Any]]:
        """
        Get all quality profiles from Sonarr.

        Returns:
            List of quality profile data dicts
        """
        if self._quality_profiles_cache is None:
            try:
                self._quality_profiles_cache = self.instance.get_quality_profile()
            except Exception as e:
                logger.error(f"Failed to get quality profiles from Sonarr: {e}")
                self._quality_profiles_cache = []
        return self._quality_profiles_cache

    def get_series_by_status(self, status: str) -> List[Dict[str, Any]]:
        """
        Get all series with a specific status.

        Args:
            status: 'continuing', 'ended', 'cancelled'

        Returns:
            List of series data dicts
        """
        all_series = self.get_series()
        return [s for s in all_series if s.get("status", "").lower() == status.lower()]

    def get_ended_series(self) -> List[Dict[str, Any]]:
        """
        Get all ended or cancelled series.

        Returns:
            List of series data dicts
        """
        all_series = self.get_series()
        return [s for s in all_series if s.get("status", "").lower() in ["ended", "cancelled"]]

    def get_continuing_series(self) -> List[Dict[str, Any]]:
        """
        Get all continuing series.

        Returns:
            List of series data dicts
        """
        all_series = self.get_series()
        return [s for s in all_series if s.get("status", "").lower() == "continuing"]

    def clear_cache(self) -> None:
        """Clear cached data."""
        self._tags_cache = None
        self._quality_profiles_cache = None
        logger.debug("Sonarr cache cleared")