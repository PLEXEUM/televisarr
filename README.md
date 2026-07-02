# 📺 Televisarr

**Intelligent TV show cleanup for Plex using Sonarr.**

Televisarr monitors your Plex watch history and Sonarr library, automatically moving fully-watched seasons (and entire series) to a "TV Leaving Soon" collection, then deleting them after a configurable grace period.

---

## Key Features

- **Season-Level Deletion** - Fully watched seasons are marked for deletion
- **Series-Level Deletion** - When ALL seasons are watched AND the series has ended, the entire series is removed
- **No Activity Deletion** - Optionally delete seasons with no watch activity for X days
- **Protection** - If someone re-watches an episode, the season is saved for X days
- **TV Leaving Soon Collection** - Single Plex collection showing what's scheduled for deletion
- **Built-in Scheduler** - Runs on a schedule automatically (daily, weekly, or custom cron)
- **Dry Run Mode** - Preview what would be deleted before enabling real deletions
- **Plex + Sonarr Only** - Focused, lightweight, no Tautulli or Radarr required

---

## Quick Start

### Docker Compose

```yaml
services:
  televisarr:
    image: ghcr.io/YOUR_USERNAME/televisarr:latest
    container_name: televisarr
    environment:
      LOG_LEVEL: INFO
    volumes:
      - ./config:/config
    restart: unless-stopped
```

---

## Configuration

Create `config/televisarr.yaml`:

```yaml
plex:
  url: "http://localhost:32400"
  token: "YOUR_PLEX_TOKEN"

sonarr:
  url: "http://localhost:8989"
  api_key: "YOUR_SONARR_API_KEY"

scheduler:
  enabled: true
  schedule: "weekly"

libraries:
  - name: "TV Shows"
    sonarr: "Sonarr"
    season:
      fully_watched:
        enabled: true
        watch_users: "any"
      no_activity:
        enabled: true
        days: 180
    grace_period: 7
    series:
      enabled: true
      require_ended: true
      grace_period: 14
    protection:
      enabled: true
      save_days: 14
    leaving_soon:
      collection_name: "TV Leaving Soon"

dry_run: true
```

---

## How It Works

### Two-Phase Deletion

1. **First Run**: Seasons/series matching deletion criteria are added to "TV Leaving Soon" collection (no deletion)
2. **Grace Period**: Items remain in the collection for X days (configurable)
3. **Second Run**: Items still in the collection are deleted from Sonarr

### Season-Level Deletion

A season is marked for deletion when:

- All episodes are fully watched (based on `watch_users` rule)
- **OR** No episodes have been watched in X days (if `no_activity` enabled)

### Series-Level Deletion

A series is marked for deletion when:

- ALL seasons are fully watched
- **AND** The series status in Sonarr is "ended" or "cancelled"

### Protection

If someone re-watches an episode in a season that's in "TV Leaving Soon":

- The entire season is removed from the collection
- The season is protected for X days (configurable)
- After X days with no new watches, it can be re-added

---

## Documentation

Full documentation available at [docs/](docs/)

---

## License

MIT License - see [LICENSE](LICENSE) for details

---

**Televisarr** – Keep your TV library fresh. 📺