"""
Pydantic schema for Televisarr configuration.

This module serves as the single source of truth for:
1. Configuration validation
2. Documentation generation
3. Type hints
"""

from typing import Optional, Literal, List
from pydantic import BaseModel, Field, model_validator


class PlexConfig(BaseModel):
    """Plex server connection settings."""

    url: str = Field(
        ...,
        description="URL of your Plex server",
        json_schema_extra={"example": "http://localhost:32400"},
    )
    token: str = Field(
        ...,
        description="Plex authentication token",
        json_schema_extra={"example": "YOUR_PLEX_TOKEN"},
    )


class SonarrInstance(BaseModel):
    """Sonarr instance connection settings."""

    name: str = Field(
        default="Sonarr",
        description="Identifier for this Sonarr instance (used in library config)",
        json_schema_extra={"example": "Sonarr"},
    )
    url: str = Field(
        ...,
        description="URL of your Sonarr server",
        json_schema_extra={"example": "http://localhost:8989"},
    )
    api_key: str = Field(
        ...,
        description="Sonarr API key",
        json_schema_extra={"example": "YOUR_SONARR_API_KEY"},
    )


class SchedulerConfig(BaseModel):
    """Built-in scheduler configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable built-in scheduler. Set to false for external schedulers",
    )
    schedule: str = Field(
        default="weekly",
        description="Cron expression or preset (hourly, daily, weekly, monthly)",
        json_schema_extra={"example": "weekly"},
    )
    timezone: str = Field(
        default="UTC",
        description="Timezone for schedule (e.g., 'America/New_York')",
        json_schema_extra={"example": "UTC"},
    )
    run_on_startup: bool = Field(
        default=False,
        description="Run immediately when container starts, in addition to scheduled runs",
    )


class SeasonConfig(BaseModel):
    """Season-level deletion rules."""

    fully_watched: dict = Field(
        default_factory=lambda: {"enabled": True, "watch_users": "any", "days": 0},
        description="Delete seasons where all episodes are fully watched. watch_users: 'any', 'all', or list of usernames. days: 0=immediate, or number of days to wait after fully watched",
        json_schema_extra={"example": {"enabled": True, "watch_users": "any", "days": 30}},
    )
    no_activity: dict = Field(
        default_factory=lambda: {"enabled": False, "days": 180},
        description="Delete seasons with no watch activity for X days",
        json_schema_extra={"example": {"enabled": True, "days": 180}},
    )
    partially_watched: dict = Field(
        default_factory=lambda: {"enabled": False, "days": 365},
        description="Delete partially watched seasons after X days",
        json_schema_extra={"example": {"enabled": False, "days": 365}},
    )


class SeriesConfig(BaseModel):
    """Series-level deletion rules."""

    enabled: bool = Field(
        default=True,
        description="Enable series-level deletion",
    )
    require_ended: bool = Field(
        default=True,
        description="Only delete series if status is 'ended' or 'cancelled'",
    )
    watch_users: str = Field(
        default="any",
        description="'any', 'all', or list of usernames",
    )
    grace_period: int = Field(
        default=14,
        description="Days in 'TV Leaving Soon' before series deletion",
        ge=1,
    )


class ProtectionConfig(BaseModel):
    """Protection settings for re-watched content."""

    enabled: bool = Field(
        default=True,
        description="Enable protection for re-watched content",
    )
    save_days: int = Field(
        default=14,
        description="Days to keep season protected after a re-watch",
        ge=1,
    )


class LeavingSoonConfig(BaseModel):
    """Configuration for the 'TV Leaving Soon' collection."""

    collection_name: str = Field(
        default="TV Leaving Soon",
        description="Name of the Plex collection",
    )
    description: str = Field(
        default="These seasons/series will be deleted soon - watch to save them!",
        description="Description shown in Plex for the collection",
    )


class LibraryConfig(BaseModel):
    """Configuration for a Plex library."""

    name: str = Field(
        ...,
        description="Name of the Plex library (must match exactly)",
        json_schema_extra={"example": "TV Shows"},
    )
    sonarr: str = Field(
        default="Sonarr",
        description="Name of the Sonarr instance to use",
        json_schema_extra={"example": "Sonarr"},
    )
    season: SeasonConfig = Field(
        default_factory=SeasonConfig,
        description="Season-level deletion rules",
    )
    grace_period: int = Field(
        default=7,
        description="Days in 'TV Leaving Soon' before season deletion",
        ge=1,
    )
    series: SeriesConfig = Field(
        default_factory=SeriesConfig,
        description="Series-level deletion rules",
    )
    protection: ProtectionConfig = Field(
        default_factory=ProtectionConfig,
        description="Protection settings for re-watched content",
    )
    leaving_soon: LeavingSoonConfig = Field(
        default_factory=LeavingSoonConfig,
        description="'TV Leaving Soon' collection settings",
    )

    @model_validator(mode="after")
    def validate_watch_users(self):
        """Validate watch_users format across season and series configs."""
        # Validate season fully_watched watch_users
        season_users = self.season.fully_watched.get("watch_users", "any")
        if season_users not in ["any", "all"] and not isinstance(season_users, list):
            raise ValueError(
                f"season.fully_watched.watch_users must be 'any', 'all', or a list of usernames. Got: {season_users}"
            )

        # Validate series watch_users
        series_users = self.series.watch_users
        if series_users not in ["any", "all"] and not isinstance(series_users, list):
            raise ValueError(
                f"series.watch_users must be 'any', 'all', or a list of usernames. Got: {series_users}"
            )

        return self


class TelevisarrConfig(BaseModel):
    """Root configuration for Televisarr."""

    # General settings
    dry_run: bool = Field(
        default=True,
        description="If true, actions are only logged, not performed",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR",
        json_schema_extra={"example": "INFO"},
    )

    # Service connections
    plex: PlexConfig = Field(
        ...,
        description="Plex server connection settings",
    )
    sonarr: SonarrInstance = Field(
        ...,
        description="Sonarr instance connection settings",
    )

    # Scheduler
    scheduler: Optional[SchedulerConfig] = Field(
        default=None,
        description="Built-in scheduler configuration",
    )

    # Libraries
    libraries: List[LibraryConfig] = Field(
        ...,
        description="Configuration for each Plex library to manage",
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_libraries(self):
        """Validate that all libraries have unique names."""
        if not self.libraries:
            raise ValueError("At least one library must be configured")

        library_names = [lib.name for lib in self.libraries]
        if len(library_names) != len(set(library_names)):
            raise ValueError("Library names must be unique")

        return self