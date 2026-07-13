"""
Plex media server integration for Televisarr.

Provides Plex API functionality for:
- Watch history (viewCount, last viewed)
- Collection management ("TV Leaving Soon")
- Label management
- Library searching and item matching
"""

from datetime import datetime
from typing import Any, Optional, List, Dict
import requests

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from televisarr import logger
from televisarr.utils import normalize_title


class PlexMediaServer:
    """Plex server connection and operations."""

    def __init__(self, url: str, token: str, ssl_verify: bool = False, timeout: int = 120):
        """
        Initialize Plex server connection.

        Args:
            url: Plex server URL
            token: Plex authentication token
            ssl_verify: Whether to verify SSL certificates
            timeout: Request timeout in seconds
        """
        session = requests.Session()
        session.verify = ssl_verify
        self.server = PlexServer(url, token, session=session, timeout=timeout)

    def test_connection(self) -> None:
        """Test the connection to Plex server."""
        self.server.library.sections()

    def get_library(self, name: str) -> Any:
        """
        Get library by name.

        Args:
            name: The name of the library

        Returns:
            Plex library section

        Raises:
            NotFound: If library doesn't exist
        """
        return self.server.library.section(name)

    def get_library_section_id(self, name: str) -> int:
        """
        Get the library section ID by name.

        Args:
            name: The name of the library

        Returns:
            Library section ID (int)

        Raises:
            NotFound: If library doesn't exist
        """
        library = self.get_library(name)
        return library.key

    def get_all_episodes(self, library: Any) -> List[Any]:
        """
        Get all episodes from a library.

        Args:
            library: Plex library section

        Returns:
            List of Plex episode items
        """
        try:
            # Search for all episodes
            return library.search(guid="episode")
        except Exception as e:
            logger.debug(f"Error getting episodes from library: {e}")
            return []

    def get_episodes_for_show(self, library: Any, show_title: str, year: Optional[int] = None) -> List[Any]:
        """
        Get all episodes for a specific show.

        Args:
            library: Plex library section
            show_title: Title of the show
            year: Optional release year for matching

        Returns:
            List of Plex episode items
        """
        try:
            # Search for the show first
            results = library.search(title=show_title)
            if not results:
                return []

            # Filter by year if provided
            show = None
            for item in results:
                if year and item.year and abs(item.year - year) <= 2:
                    show = item
                    break
                elif not year:
                    show = item
                    break

            if not show:
                return []

            # Get all episodes
            return show.episodes()
        except Exception as e:
            logger.debug(f"Error getting episodes for show '{show_title}': {e}")
            return []

    def get_watch_history(self, library_section_id: int) -> Dict[str, Dict]:
        """
        Get watch history for a library section.

        Returns a dict keyed by episode ratingKey with watch data.

        Args:
            library_section_id: Plex library section ID

        Returns:
            Dict mapping ratingKey to {last_watched, view_count, title, year}
        """
        try:
            history = self.server.history(
                librarySectionID=library_section_id,
                maxresults=100000
            )
        except Exception as e:
            logger.warning(f"Failed to get watch history: {e}")
            return {}

        if not history:
            return {}

        watch_data = {}
        for item in history:
            if item.type != "episode":
                continue

            viewed_at = item.viewedAt
            if not viewed_at:
                continue

            if isinstance(viewed_at, datetime):
                last_watched = viewed_at
            else:
                last_watched = datetime.fromtimestamp(float(viewed_at))

            rating_key = str(item.ratingKey)
            grandparent_key = str(item.grandparentRatingKey) if item.grandparentRatingKey else None

            entry = {
                "last_watched": last_watched,
                "view_count": item.viewCount or 1,
                "title": item.title,
                "show_title": item.grandparentTitle,
                "year": getattr(item, 'grandparentYear', None) or getattr(item, 'year', None),
                "season_number": item.seasonNumber,
                "episode_number": item.episodeNumber,
                "rating_key": rating_key,
                "grandparent_key": grandparent_key,
            }

            # Store by episode rating key
            if rating_key not in watch_data or last_watched > watch_data[rating_key]["last_watched"]:
                watch_data[rating_key] = entry

            # Also store by grandparent key for season-level lookups
            if grandparent_key:
                if grandparent_key not in watch_data or last_watched > watch_data[grandparent_key]["last_watched"]:
                    # For season-level, we store the most recent watch
                    watch_data[grandparent_key] = entry

        logger.debug(f"Processed {len(watch_data)} watch history entries")
        return watch_data

    def get_season_watch_status(
        self,
        library: Any,
        show_title: str,
        season_number: int,
        year: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get watch status for a specific season.

        Args:
            library: Plex library section
            show_title: Title of the show
            season_number: Season number to check
            year: Optional release year for matching

        Returns:
            Dict with:
                - total_episodes: Total episodes in season
                - watched_episodes: Number of fully watched episodes
                - all_watched: True if all episodes are watched
                - last_watched: Most recent watch date (or None)
        """
        episodes = self.get_episodes_for_show(library, show_title, year)
        season_episodes = [ep for ep in episodes if ep.seasonNumber == season_number]

        if not season_episodes:
            return {
                "total_episodes": 0,
                "watched_episodes": 0,
                "all_watched": False,
                "last_watched": None,
                "episodes": []
            }

        watched_count = 0
        last_watched = None
        episode_status = []

        for episode in season_episodes:
            is_watched = episode.viewCount is not None and episode.viewCount > 0
            episode_status.append({
                "episode": episode,
                "view_count": episode.viewCount or 0,
                "is_watched": is_watched,
            })

            if is_watched:
                watched_count += 1
                if episode.viewedAt:
                    if last_watched is None or episode.viewedAt > last_watched:
                        last_watched = episode.viewedAt

        return {
            "total_episodes": len(season_episodes),
            "watched_episodes": watched_count,
            "all_watched": watched_count == len(season_episodes),
            "last_watched": last_watched,
            "episodes": episode_status,
        }

    def get_or_create_collection(
        self,
        library: Any,
        name: str,
        items: Optional[List[Any]] = None,
        description: Optional[str] = None
    ) -> Optional[Any]:
        """Get existing collection or create a new one."""
        try:
            collection = library.collection(name)
            if description:
                try:
                    collection.editSummary(description)
                except Exception:
                    pass
            return collection
        except NotFound:
            if items:
                logger.debug(f"Creating new collection '{name}' with {len(items)} items")
                collection = library.createCollection(title=name, smart=False, items=items)
                if description:
                    try:
                        collection.editSummary(description)
                    except Exception:
                        pass
                return collection
            else:
                logger.debug(f"Collection '{name}' does not exist and no items to add, skipping creation")
                return None

    def set_collection_items(self, collection: Any, items: List[Any]) -> None:
        """
        Replace collection contents with given items.

        Args:
            collection: Plex collection
            items: List of Plex media items to set in the collection
        """
        # Get current items
        try:
            current_items = collection.items()
        except Exception:
            current_items = []

        # Remove all current items
        if current_items:
            try:
                collection.removeItems(current_items)
            except Exception as e:
                logger.warning(
                    f"Error removing {len(current_items)} items from collection '{collection.title}': {e}"
                )

        # Add new items
        if items:
            try:
                collection.addItems(items)
            except Exception as e:
                logger.warning(
                    f"Error adding {len(items)} items to collection '{collection.title}': {e}"
                )

    def set_collection_visibility(
        self,
        collection: Any,
        home: bool = True,
        shared: bool = True
    ) -> None:
        """
        Set collection visibility on home screens.

        Args:
            collection: Plex collection
            home: Whether to show on owner's Home page
            shared: Whether to show on shared users' Home pages
        """
        try:
            hub = collection.visibility()
            hub.updateVisibility(home=home, shared=shared)
            logger.debug(f"Set collection '{collection.title}' visibility: home={home}, shared={shared}")
        except Exception as e:
            logger.warning(f"Could not set visibility for collection '{collection.title}': {e}")

    def get_collection_items(self, library: Any, name: str) -> List[Any]:
        """
        Get all items in a collection.

        Args:
            library: Plex library section
            name: Name of the collection

        Returns:
            List of Plex items in the collection
        """
        try:
            collection = library.collection(name)
            return collection.items()
        except NotFound:
            return []
        except Exception as e:
            logger.warning(f"Error getting collection '{name}': {e}")
            return []

    def clear_collection(self, library: Any, name: str) -> bool:
        """
        Clear all items from a collection.

        Args:
            library: Plex library section
            name: Name of the collection

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            collection = library.collection(name)
            items = collection.items()
            if items:
                collection.removeItems(items)
            return True
        except NotFound:
            return True  # Collection doesn't exist, nothing to clear
        except Exception as e:
            logger.warning(f"Error clearing collection '{name}': {e}")
            return False

    # ========== NEW LABEL METHODS - ADD HERE ==========

    def add_label(self, item: Any, label: str) -> None:
        """Add a label to a Plex media item.

        Args:
            item: The Plex media item.
            label: The label to add.
        """
        try:
            item.addLabel(label)
        except Exception as e:
            logger.debug(f"Could not add label '{label}' to '{item.title}': {e}")

    def remove_label(self, item: Any, label: str) -> None:
        """Remove a label from a Plex media item.

        Args:
            item: The Plex media item.
            label: The label to remove.
        """
        try:
            item.removeLabel(label)
        except Exception as e:
            logger.debug(f"Could not remove label '{label}' from '{item.title}': {e}")

    def get_items_with_label(self, library: Any, label: str) -> List[Any]:
        """Get all items in library with given label.

        Args:
            library: The Plex library section.
            label: The label to search for.

        Returns:
            List of Plex media items with the label.
        """
        try:
            return library.search(label=label)
        except Exception as e:
            logger.warning(
                f"Error searching for items with label '{label}' in library '{library.title}': {e}"
            )
            return []

    def remove_label_from_all_items(self, library: Any, label: str) -> int:
        """Remove a label from all items in a library.

        Args:
            library: The Plex library section.
            label: The label to remove.

        Returns:
            Number of items that had the label removed.
        """
        try:
            items = library.search(label=label)
            count = 0
            for item in items:
                try:
                    item.removeLabel(label)
                    count += 1
                except Exception:
                    pass
            logger.debug(f"Removed label '{label}' from {count} items in library '{library.title}'")
            return count
        except Exception as e:
            logger.warning(f"Error removing label '{label}' from library '{library.title}': {e}")
            return 0

    # ========== END LABEL METHODS ==========

    def find_show(
        self,
        library: Any,
        title: str,
        year: Optional[int] = None,
        tvdb_id: Optional[int] = None
    ) -> Optional[Any]:
        """
        Find a show by title, year, or TVDB ID.

        Args:
            library: Plex library section
            title: Title of the show
            year: Optional release year
            tvdb_id: Optional TVDB ID

        Returns:
            Plex show item or None
        """
        # Try TVDB first
        if tvdb_id:
            try:
                results = library.search(guid=f"tvdb://{tvdb_id}")
                if results:
                    return results[0]
            except Exception:
                pass

        # Try title search
        if title:
            try:
                results = library.search(title=title)
                for item in results:
                    # Check if it's a show (not episode/movie)
                    if hasattr(item, 'type') and item.type != 'show':
                        continue
                    # Check year if provided
                    if year and item.year and abs(item.year - year) <= 2:
                        return item
                    elif not year:
                        return item
            except Exception as e:
                logger.debug(f"Error searching for show '{title}': {e}")

        return None

    def get_show_episodes(
        self,
        library: Any,
        show_title: str,
        year: Optional[int] = None,
        tvdb_id: Optional[int] = None
    ) -> List[Any]:
        """
        Get all episodes for a show.

        Args:
            library: Plex library section
            show_title: Title of the show
            year: Optional release year
            tvdb_id: Optional TVDB ID

        Returns:
            List of Plex episode items
        """
        show = self.find_show(library, show_title, year, tvdb_id)
        if not show:
            return []
        
        try:
            return show.episodes()
        except Exception as e:
            logger.debug(f"Error getting episodes for show '{show_title}': {e}")
            return []

    def get_show_seasons(
        self,
        library: Any,
        show_title: str,
        year: Optional[int] = None,
        tvdb_id: Optional[int] = None
    ) -> Dict[int, List[Any]]:
        """
        Get episodes grouped by season for a show.

        Args:
            library: Plex library section
            show_title: Title of the show
            year: Optional release year
            tvdb_id: Optional TVDB ID

        Returns:
            Dict mapping season number to list of episodes
        """
        episodes = self.get_show_episodes(library, show_title, year, tvdb_id)
        seasons = {}
        
        for episode in episodes:
            season_num = getattr(episode, 'seasonNumber', None)
            if season_num is not None:
                if season_num not in seasons:
                    seasons[season_num] = []
                seasons[season_num].append(episode)
        
        return seasons

    def get_show_season_watch_status(
        self,
        library: Any,
        show_title: str,
        season_number: int,
        year: Optional[int] = None,
        tvdb_id: Optional[int] = None,
        watch_history: Optional[Dict[str, Dict]] = None  # ✅ Add this parameter
    ) -> Dict[str, Any]:
        """
        Get watch status for a specific season of a show.
    
        Uses watch_history dict if provided, otherwise falls back to episode viewCount.
        """
        episodes = self.get_show_episodes(library, show_title, year, tvdb_id)
        season_episodes = [ep for ep in episodes if getattr(ep, 'seasonNumber', None) == season_number]

        if not season_episodes:
            return {
                "total_episodes": 0,
                "watched_episodes": 0,
                "all_watched": False,
                "last_watched": None,
                "no_activity": True,
            }

        watched_count = 0
        last_watched = None

        if watch_history:
            # Use watch history dict for accurate data
            for episode in season_episodes:
                rating_key = str(episode.ratingKey)
                if rating_key in watch_history:
                    watched_count += 1
                    hist_date = watch_history[rating_key]["last_watched"]
                    if last_watched is None or hist_date > last_watched:
                        last_watched = hist_date
        else:
            # Fallback to viewCount (less reliable)
            for episode in season_episodes:
                is_watched = episode.viewCount is not None and episode.viewCount > 0
                if is_watched:
                    watched_count += 1
                    if episode.viewedAt:
                        if last_watched is None or episode.viewedAt > last_watched:
                            last_watched = episode.viewedAt

        return {
            "total_episodes": len(season_episodes),
            "watched_episodes": watched_count,
            "all_watched": watched_count == len(season_episodes) and len(season_episodes) > 0,
            "last_watched": last_watched,
            "no_activity": watched_count == 0,
        }


    def has_episode_been_watched(self, episode: Any) -> bool:
        """
        Check if a specific episode has been watched.

        Args:
            episode: Plex episode item

        Returns:
            True if the episode has been watched
        """
        return episode.viewCount is not None and episode.viewCount > 0

    def get_last_watched_date(self, item: Any) -> Optional[datetime]:
        """
        Get the last watched date for an item.

        Args:
            item: Plex item (episode, season, or show)

        Returns:
            datetime of last watch, or None if never watched
        """
        if hasattr(item, 'viewedAt') and item.viewedAt:
            if isinstance(item.viewedAt, datetime):
                return item.viewedAt
            try:
                return datetime.fromtimestamp(float(item.viewedAt))
            except (ValueError, TypeError):
                return None
        return None