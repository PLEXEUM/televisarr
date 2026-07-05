"""
State management for Televisarr.

Tracks items in the "TV Leaving Soon" collection to enable:
- Two-phase deletion (tag first, delete later)
- Grace period tracking
- Protection tracking (re-watched items)
- Determining which seasons/series are newly tagged vs. already in collection
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from televisarr import logger

STATE_FILE = "/config/.televisarr_state.json"
STATE_VERSION = 1


class StateManager:
    """
    Manages persistent state for TV Leaving Soon tracking.

    State file format:
    {
        "version": 1,
        "leaving_soon": {
            "TV Shows": {
                "season": {
                    "series_id_1": {
                        "1": {  # season number
                            "tagged_at": "2026-03-01T13:27:49",
                            "protection_until": null
                        }
                    }
                },
                "series": {
                    "series_id_1": {
                        "tagged_at": "2026-03-01T13:27:49",
                        "protection_until": null
                    }
                }
            }
        }
    }
    """

    def __init__(self, state_file: str = STATE_FILE):
        """Initialize the state manager."""
        self._state_file = state_file

    def load(self) -> dict:
        """Load state from file, return empty state if missing or corrupt."""
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
                logger.warning("State file has unexpected format or version, starting fresh")
                return self._empty_state()
            return data
        except FileNotFoundError:
            return self._empty_state()
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read state file '{self._state_file}': {e}. Starting fresh.")
            return self._empty_state()

    def save(self, state: dict) -> None:
        """Atomically save state to file (write to temp, then rename)."""
        state_dir = os.path.dirname(self._state_file)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=state_dir, prefix=".televisarr_state_", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2, default=str)
                os.replace(tmp_path, self._state_file)
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.error(f"Failed to save state file '{self._state_file}': {e}")

    @staticmethod
    def _empty_state() -> dict:
        """Return an empty state dictionary."""
        return {"version": STATE_VERSION, "leaving_soon": {}}

    # ========== Season State Methods ==========

    def get_season_state(self, library_name: str, series_id: int, season_number: int) -> Optional[dict]:
        """
        Get the state for a specific season.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            Dict with tagged_at and protection_until, or None if not found
        """
        state = self.load()
        library_state = state.get("leaving_soon", {}).get(library_name, {})
        season_state = library_state.get("season", {})
        series_state = season_state.get(str(series_id), {})
        return series_state.get(str(season_number))

    def get_all_tagged_seasons(self, library_name: str) -> Dict[int, Dict[int, dict]]:
        """
        Get all tagged seasons for a library.

        Args:
            library_name: Name of the Plex library

        Returns:
            Dict mapping series_id -> {season_number: state}
        """
        state = self.load()
        library_state = state.get("leaving_soon", {}).get(library_name, {})
        return library_state.get("season", {})

    def tag_season(
        self,
        library_name: str,
        series_id: int,
        season_number: int,
        protection_until: Optional[datetime] = None
    ) -> None:
        """
        Tag a season as "leaving soon".

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
            season_number: Season number
            protection_until: Optional protection expiry date
        """
        state = self.load()
        library_state = state.setdefault("leaving_soon", {}).setdefault(library_name, {})
        season_state = library_state.setdefault("season", {})
        series_state = season_state.setdefault(str(series_id), {})

        # Only set if not already tagged (preserve original tagged_at)
        if str(season_number) not in series_state:
            series_state[str(season_number)] = {
                "tagged_at": datetime.now().isoformat(),
                "protection_until": protection_until.isoformat() if protection_until else None,
            }
            self.save(state)
            logger.debug(f"Tagged season {season_number} for series {series_id} in library '{library_name}'")

    def untag_season(
        self,
        library_name: str,
        series_id: int,
        season_number: int
    ) -> None:
        """
        Remove a season from the "leaving soon" state.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
            season_number: Season number
        """
        state = self.load()
        library_state = state.get("leaving_soon", {}).get(library_name, {})
        season_state = library_state.get("season", {})
        series_state = season_state.get(str(series_id), {})

        if str(season_number) in series_state:
            del series_state[str(season_number)]
            # Clean up empty entries
            if not series_state:
                del season_state[str(series_id)]
            if not season_state:
                del library_state["season"]
            if not library_state:
                del state["leaving_soon"][library_name]
            self.save(state)
            logger.debug(f"Untagged season {season_number} for series {series_id} in library '{library_name}'")

    def protect_season(
        self,
        library_name: str,
        series_id: int,
        season_number: int,
        save_days: int
    ) -> None:
        """
        Protect a season from being re-tagged for a period of time.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
            season_number: Season number
            save_days: Number of days to protect for
        """
        state = self.load()
        library_state = state.setdefault("leaving_soon", {}).setdefault(library_name, {})
        season_state = library_state.setdefault("season", {})
        series_state = season_state.setdefault(str(series_id), {})

        protection_until = datetime.now() + timedelta(days=save_days)

        if str(season_number) in series_state:
            series_state[str(season_number)]["protection_until"] = protection_until.isoformat()
        else:
            # Season might not be tagged yet, but we still track protection
            series_state[str(season_number)] = {
                "tagged_at": None,
                "protection_until": protection_until.isoformat(),
            }

        self.save(state)
        logger.debug(
            f"Season {season_number} for series {series_id} protected until {protection_until.isoformat()}"
        )

    def is_season_protected(
        self,
        library_name: str,
        series_id: int,
        season_number: int
    ) -> bool:
        """
        Check if a season is currently protected.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            True if the season is protected, False otherwise
        """
        season_state = self.get_season_state(library_name, series_id, season_number)
        if not season_state:
            return False

        protection_until = season_state.get("protection_until")
        if not protection_until:
            return False

        try:
            protection_date = datetime.fromisoformat(protection_until)
            return datetime.now() < protection_date
        except (ValueError, TypeError):
            return False

    def get_season_tagged_at(
        self,
        library_name: str,
        series_id: int,
        season_number: int
    ) -> Optional[datetime]:
        """
        Get the date when a season was tagged.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
            season_number: Season number

        Returns:
            Tagged date, or None if not found
        """
        season_state = self.get_season_state(library_name, series_id, season_number)
        if not season_state:
            return None

        tagged_at = season_state.get("tagged_at")
        if not tagged_at:
            return None

        try:
            return datetime.fromisoformat(tagged_at)
        except (ValueError, TypeError):
            return None

    def get_seasons_tagged_before(
        self,
        library_name: str,
        cutoff_date: datetime
    ) -> List[tuple]:
        """
        Get all seasons tagged before a specific date.

        Args:
            library_name: Name of the Plex library
            cutoff_date: Date to compare against

        Returns:
            List of (series_id, season_number, tagged_at) tuples
        """
        result = []
        tagged_seasons = self.get_all_tagged_seasons(library_name)

        for series_id_str, seasons in tagged_seasons.items():
            series_id = int(series_id_str)
            for season_num_str, state in seasons.items():
                tagged_at = state.get("tagged_at")
                if not tagged_at:
                    continue
                try:
                    tagged_date = datetime.fromisoformat(tagged_at)
                    if tagged_date < cutoff_date:
                        result.append((series_id, int(season_num_str), tagged_date))
                except (ValueError, TypeError):
                    continue

        return result

    # ========== Series State Methods ==========

    def get_series_state(self, library_name: str, series_id: int) -> Optional[dict]:
        """
        Get the state for a specific series.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID

        Returns:
            Dict with tagged_at and protection_until, or None if not found
        """
        state = self.load()
        library_state = state.get("leaving_soon", {}).get(library_name, {})
        series_state = library_state.get("series", {})
        return series_state.get(str(series_id))

    def tag_series(self, library_name: str, series_id: int) -> None:
        """
        Tag a series as "leaving soon".

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
        """
        state = self.load()
        library_state = state.setdefault("leaving_soon", {}).setdefault(library_name, {})
        series_state = library_state.setdefault("series", {})

        if str(series_id) not in series_state:
            series_state[str(series_id)] = {
                "tagged_at": datetime.now().isoformat(),
            }
            self.save(state)
            logger.debug(f"Tagged series {series_id} in library '{library_name}'")

    def untag_series(self, library_name: str, series_id: int) -> None:
        """
        Remove a series from the "leaving soon" state.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
        """
        state = self.load()
        library_state = state.get("leaving_soon", {}).get(library_name, {})
        series_state = library_state.get("series", {})

        if str(series_id) in series_state:
            del series_state[str(series_id)]
            if not series_state:
                del library_state["series"]
            if not library_state:
                del state["leaving_soon"][library_name]
            self.save(state)
            logger.debug(f"Untagged series {series_id} in library '{library_name}'")

    def is_series_tagged(self, library_name: str, series_id: int) -> bool:
        """
        Check if a series is currently tagged.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID

        Returns:
            True if the series is tagged, False otherwise
        """
        return self.get_series_state(library_name, series_id) is not None

    def get_all_tagged_series(self, library_name: str) -> Dict[int, dict]:
        """
        Get all tagged series for a library.

        Args:
            library_name: Name of the Plex library

        Returns:
            Dict mapping series_id -> state
        """
        state = self.load()
        library_state = state.get("leaving_soon", {}).get(library_name, {})
        series_state = library_state.get("series", {})
        return {int(k): v for k, v in series_state.items()}

    def get_series_tagged_at(self, library_name: str, series_id: int) -> Optional[datetime]:
        """
        Get the date when a series was tagged.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID

        Returns:
            Tagged date, or None if not found
        """
        series_state = self.get_series_state(library_name, series_id)
        if not series_state:
            return None

        tagged_at = series_state.get("tagged_at")
        if not tagged_at:
            return None

        try:
            return datetime.fromisoformat(tagged_at)
        except (ValueError, TypeError):
            return None

    # ========== Cleanup Methods ==========

    def cleanup_library(self, library_name: str) -> None:
        """
        Clean up state for a library that no longer exists.

        Args:
            library_name: Name of the Plex library
        """
        state = self.load()
        if library_name in state.get("leaving_soon", {}):
            del state["leaving_soon"][library_name]
            self.save(state)
            logger.debug(f"Cleaned up state for library '{library_name}'")

    def cleanup_stale_entries(
        self,
        library_name: str,
        active_series_ids: Set[int],
        active_seasons: Dict[int, Set[int]]
    ) -> None:
        """
        Remove state entries for series/seasons that no longer exist.

        Args:
            library_name: Name of the Plex library
            active_series_ids: Set of active series IDs
            active_seasons: Dict mapping series_id -> set of active season numbers
        """
        state = self.load()
        library_state = state.get("leaving_soon", {}).get(library_name, {})
        if not library_state:
            return

        modified = False

        # Clean up seasons
        season_state = library_state.get("season", {})
        for series_id_str in list(season_state.keys()):
            series_id = int(series_id_str)
            if series_id not in active_series_ids:
                del season_state[series_id_str]
                modified = True
                continue

            active_season_numbers = active_seasons.get(series_id, set())
            series_seasons = season_state[series_id_str]
            for season_num_str in list(series_seasons.keys()):
                season_num = int(season_num_str)
                if season_num not in active_season_numbers:
                    del series_seasons[season_num_str]
                    modified = True

            if not series_seasons:
                del season_state[series_id_str]
                modified = True

        if modified:
            self.save(state)
            logger.debug(f"Cleaned up stale state entries for library '{library_name}'")

    # ========== Utility Methods ==========

    def get_collection_name(self, library_name: str) -> Optional[str]:
        """
        Get the stored collection name for a library.
    
        Args:
            library_name: Name of the Plex library
        
        Returns:
            Stored collection name, or None if not found
        """
        state = self.load()
        library_state = state.get("leaving_soon", {}).get(library_name, {})
        return library_state.get("collection_name")

    def set_collection_name(self, library_name: str, collection_name: str) -> None:
        """
        Store the collection name for a library.
    
        Args:
            library_name: Name of the Plex library
            collection_name: Collection name to store
        """
        state = self.load()
        library_state = state.setdefault("leaving_soon", {}).setdefault(library_name, {})
        library_state["collection_name"] = collection_name
        self.save(state)
        logger.debug(f"Stored collection name '{collection_name}' for library '{library_name}'")
    
    def is_item_in_leaving_soon(
        self,
        library_name: str,
        series_id: int,
        season_number: Optional[int] = None
    ) -> bool:
        """
        Check if an item is in the "leaving soon" state.

        Args:
            library_name: Name of the Plex library
            series_id: Sonarr series ID
            season_number: Optional season number (if None, checks series level)

        Returns:
            True if the item is tagged, False otherwise
        """
        if season_number is not None:
            return self.get_season_state(library_name, series_id, season_number) is not None
        else:
            return self.get_series_state(library_name, series_id) is not None