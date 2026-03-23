# Changelog

All notable changes to **tidmon** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [1.1.8] — 2026-03-22

### Fixed

- **Skip Various Artists compilations during artist download and refresh** (`cmd/download.py`, `cmd/refresh.py`)
  — When downloading an artist's discography or refreshing monitored artists, albums
  whose primary artist is "Various Artists" / "Varios Artistas" / "Varios" are now
  skipped automatically. This prevents Tidal editorial compilations from being
  downloaded as part of a regular artist's discography.
  - `_download_artist_async`: skips and logs a summary of skipped compilations.
  - `_refresh_artist`: skips Various Artists albums before adding them to the DB.

---

## [1.1.2] — 2026-03-18

### Added

- **`tidmon show report`** (`cmd/show.py`, `cli.py`)
  — New command that displays a per-artist summary of album count and total track
  count (songs) from the local SQLite database.
  - Console output: Rich table with artist ID, name, album count, song count, and a
    totals footer row.
  - `--export <FILE>`: exports to CSV (utf-8-sig BOM for Excel compatibility) when
    the extension is `.csv`, or to a dark-themed HTML table when the extension is
    `.html`.

- **`get_artist_stats()`** (`core/db.py`)
  — SQL query using `LEFT JOIN albums … GROUP BY artist_id` to aggregate album count
  and `SUM(number_of_tracks)` per active artist, ordered alphabetically.

### Fixed

- **`UnicodeEncodeError` on Windows cp1252 terminals** (`cmd/show.py`)
  — Removed emoji characters from `console.print()` export confirmation messages
  that caused crashes on legacy Windows terminals.

---

## [1.1.1] — 2026-03-15

### Changed

- **`DEFAULT_ARTIST_SEPARATOR` constant** (`core/utils/format.py`)
  — `", "` is now defined as a module-level constant and used as the parameter default in
  `generate_template_data`, `format_path`, `add_track_metadata`, and all
  `config.get("artist_separator", …)` call-sites. Eliminates drift between the config
  default and the hard-coded string.

- **`build_artist_string()` shared helper** (`core/utils/format.py`)
  — Artist-building logic (sort MAIN/FEATURED separately → join with separator →
  fallback to all artists → last-resort `track.artist`) is now in one place.
  `add_track_metadata` in `metadata.py` imports and calls the helper instead of
  duplicating the logic. Future callers follow the same path automatically.

---

## [1.1.0] — 2026-03-14

### Added

- **Production-grade Tidal rate limiting** (`core/client.py`)
  — Best-of-all-three strategy combining improvements from streamrip and tiddl:

  - **`threading.Lock` fixed-interval gate** — Serialises all threads through a
    single gate (`60 / rpm` seconds). Per-request jitter (`random.uniform(0, 0.3)`)
    makes traffic patterns unpredictable to the API.
  - **Adaptive delay** (`_rate_limit_delay`) — Float maintained per client. HTTP 429
    increments it by `1.0 s` (max `5.0 s`); every successful response decrements it
    by `0.1 s` (floor `0.0 s`). Applied before the fixed-interval gate.
  - **Cache-hit slot release** — When `requests_cache` returns a cached response
    (`response.from_cache == True`), the rate-limit clock is rewound by one interval
    so cache hits never consume real API quota.
  - **Configurable `requests_per_minute`** — Default `50`. Read from `config.json`.

- **`"requests_per_minute"` in `DEFAULT_CONFIG`** (`core/config.py`)
  — New key with default `50`. Existing configs continue to work unchanged (the key
  is optional; any missing key falls back to the default).

### Changed

- **`TidalClient.__init__`** (`core/client.py`)
  — Accepts `requests_per_minute` parameter; passed from `auth.py` via
  `Config().get("requests_per_minute", 50)`.

---

## [1.0.0] — 2026-03-01

### Added

Initial public release of **tidmon-cli** — a command-line tool for monitoring TIDAL
artists, tracking new releases, and automating music library management.

#### Core features
- Artist & playlist monitoring with a local SQLite database
- `tidmon refresh --download` to check for new releases and download them
- Download by artist, album, track, or URL
- Hi-Res FLAC (MAX) with automatic quality fallback
- Sequential downloader: audio → lyrics → metadata → cover per track
- Flexible path-template system (same variables as tiddl)
- Token refresh handled automatically for long-running sessions
- `tidmon backup` / `tidmon restore` for database and config snapshots
- E-mail notifications on new releases (SMTP)

#### Configuration
- `config.json` in the platform data directory (`%APPDATA%\tidmon\` on Windows,
  `~/.local/share/tidmon/` on Linux/macOS)
- All keys are optional; missing keys fall back to documented defaults

#### Documentation
- README.md, COMMANDS.md, CONFIG_GUIDE.md

---

> This project is not affiliated with TIDAL.
> For personal and archival use only.
