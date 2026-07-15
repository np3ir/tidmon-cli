# Changelog

All notable changes to **tidmon** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased] — 2026-07-15

### Added

- **`tidmon favorite`** (`cmd/favorite.py`, `cli.py`)
  — sync tidmon's library to your TIDAL account favorites: follow every monitored
  artist (`POST users/{id}/favorites/artists`, `artistIds` comma-batched) and add every
  monitored playlist (`favorites/playlists`, `uuids`). Reads current favorites first, so
  it only adds what's missing and is safe to re-run. Options: `--artists/--no-artists`,
  `--playlists/--no-playlists`, `--chunk-size` (default 100), `--pause` (default 1.0s).
  Proactive token refresh + one 401 retry mid-run.
- **`tidmon follow`** (`cmd/favorite.py`, `cli.py`)
  — follow specific artists by numeric ID or by name (resolved against the local DB),
  or from a `--file` (one artist per line; ID or name; `#` comments). Batched POST.
- **`tidmon unfollow`** (`cmd/favorite.py`, `cli.py`)
  — stop following artists by ID/name/`--file`, or `--all` to clear every followed
  artist (behind a confirmation prompt). `DELETE users/{id}/favorites/artists/{artistId}`
  per artist. Lets you curate which artists occupy TIDAL's 10,000-favorite cap.

### Notes

- **TIDAL caps favorite artists at 10,000** (API `subStatus 7004`). `favorite`/`follow`
  fill up to the cap and then stop with a clear message instead of hammering doomed
  calls. Playlists have no comparable practical limit.

---

## [Unreleased] — 2026-07-07

### Added

- **`tidmon playlist artists <URL>`** (`cmd/playlist.py`, `cli.py`)
  — read-only lookup that lists every unique artist appearing in a playlist's current
  tracks, mirroring `tidmon playlist albums`. Supports `--export FILE` to write one
  `tiddl download url https://tidal.com/artist/<id>` command per unique artist,
  same convention as `playlist albums --export` (`tiddl download url` accepts artist
  resource URLs and pulls their full discography).

### Removed

- **`tidmon monitor playlist export`** — moved to `tidmon playlist artists --export`,
  which lives alongside the other read-only playlist inspection commands instead of
  under `monitor playlist`. Same CSV output format.

## [Unreleased] — 2026-06-25

### Added

- **`refresh --restart`** (`cli.py`, `cmd/refresh.py`, `core/db.py`)
  — start a refresh from the beginning instead of resuming. Clears every artist's
  `last_checked` (`db.reset_all_check_times()`) so all monitored artists are re-checked
  from the top in `artist_name` order. Non-destructive: it only resets the progress
  timestamps and does **not** delete artists or albums (unlike `tidmon reset`).

### Changed

- **Faster refresh pacing, same anti-bot safety** (`core/client.py`)
  — the per-request interval jitter was recentred from `uniform(0.6, 1.9)` (mean 1.25×
  the configured interval) to `uniform(0.5, 1.5)` (mean 1.0×). The irregular cadence
  that defeats bot-detection comes from the variance, not from an inflated mean, so this
  restores the configured `requests_per_minute` (~20% faster) while keeping the request
  pattern just as non-bot-like. The occasional longer "human" pause is unchanged.

### Fixed

- **Doubled rate-limit backoff after 429s** (`core/api.py`, `core/client.py`)
  — `_fetch_with_retry` kept its own adaptive `_rate_limit_delay` that stacked on top of
  the identical one in `TidalClientImproved.fetch()`, so each request after a 429 could
  sleep up to ~10s extra instead of ~5s. The client is now the single rate-limit
  authority; `api.py` retains only the retry/exponential-backoff loop.

---

## [Unreleased] — 2026-06-23

### Added

- **Login-free detection (anonymous refresh)** (`core/client.py`, `core/auth.py`,
  `cmd/refresh.py`, `cmd/monitor.py`, `cli.py`)
  — `refresh` and `monitor` now feed the database **without using your account**.
  Catalogue reads (artists, albums, videos, search) go out strictly via the public
  `x-tidal-token` path, so polling never attaches, refreshes, or rotates your personal
  OAuth token — eliminating the risk of getting the account flagged or soft-blocked
  during large runs. `TidalClientImproved(anonymous=True)` drops the Bearer fallback
  (a failed public call raises instead of retrying with the account token) and refuses
  auth-only endpoints (`playbackinfo`/`playback`/`logout`/`token`/`events`). Login is
  now optional and only required for **downloading** (streams need a subscriber session).

### Changed

- **`refresh` and `monitor` default to anonymous** — pass `--use-account` to `tidmon
  refresh` to restore the previous behaviour (account Bearer + token-expiry fallback),
  e.g. if anonymous public access is ever blocked. `--download` is unaffected: the
  downloader still uses your logged-in account.

---

## [Unreleased] — 2026-06-14

### Added

- **Resumable refresh** (`core/db.py`, `cmd/refresh.py`, `cli.py`)
  — `get_all_artists` now orders by `last_checked ASC` (never-checked first), so an
  interrupted `tidmon refresh` continues with the still-pending artists on the next
  run instead of restarting from the top. New `--resume` (skip artists checked in the
  last 18h) and `--stale-hours N` (only artists not checked in the last N hours, or
  never) flags let you resume or chunk a large refresh precisely.
- **Volume cap and pacing** (`cmd/refresh.py`, `cli.py`)
  — `--max-artists N` processes at most N artists per run, plus a random 20–60s pause
  every 250 artists, to break the constant-rate pattern that can trigger bot detection.

### Changed

- **Anti-bot circuit breaker** (`cmd/refresh.py`, `core/api.py`)
  — `refresh` now aborts after 10 consecutive per-artist API failures (likely a
  systemic block — DataDome/bot protection, suspended account or network) instead of
  hammering every remaining artist, which only reinforces an IP block. The API client
  also detects the DataDome signature (`datadome` / `bot_protection` /
  `you have been blocked` / `abuse`) in `403` responses and logs it as an error.
  `_refresh_artist` now returns a success boolean to drive the breaker.
- **Randomized request pacing** (`core/client.py`)
  — Each request now waits a randomized interval (mean ~1.25x the configured
  `requests_per_minute`, with an occasional longer "human" pause) instead of a
  near-constant cadence, so the traffic pattern is far less recognizable as a bot.

---

## [Unreleased] — 2026-05-16

### Fixed

- **Video ARTIST tag: multi-value list** (`core/utils/metadata.py`)
  — `add_video_metadata()` now builds a sorted MAIN+FEATURED artist list and passes it
  to `add_m4a_metadata()` as a list (multi-value `©ART` atom). Previously used
  `";".join(...)` — a semicolon-separated single string inconsistent with music tracks
  and other downloaders.

---

## [Unreleased] — 2026-05-15

### Changed

- **Folder date format: year only** (`core/config.py`)
  — Album folders now use `({year})` instead of `({YYYY-MM-DD})` to match OrpheusDL and
  streamrip output. Example: `A/Aitana/(2025) CUARTO AZUL (ALBUM)/` instead of
  `A/Aitana/(2025-05-15) CUARTO AZUL (ALBUM)/`. Applies to both audio and video templates.

- **Artist separator unified to ` / `** (`core/config.py`, `core/utils/format.py`)
  — Default artist separator changed from `, ` to ` / `, which the filename sanitizer
  converts to the fullwidth `／` (U+FF0F). Files now read `Reik ／ Ozuna ／ Wisin`
  instead of `Reik, Ozuna, Wisin` — consistent with tiddl and OrpheusDL output.

- **Multi-value ARTIST tag** (`core/utils/metadata.py`)
  — ARTIST tag in FLAC (Vorbis Comment) and M4A (`©ART` atom) is now written as a list
  of individual artist names instead of a single joined string. Each artist gets its own
  tag entry (e.g. `ARTIST=Reik`, `ARTIST=Ozuna`, `ARTIST=Wisin`). Tag readers that
  support multi-value display them correctly; others join with their own separator.
  Consistent with tiddl and OrpheusDL behavior.

---

## [1.3.0] — 2026-04-19

### Added

- **`tidmon download pending-videos`** (`cli.py`, `cmd/download.py`)
  — New command to download all videos in the DB that have not been downloaded yet.
  - `--dry-run` — shows pending videos without downloading.
  - `--force` — re-downloads even if file already exists on disk.
  - `--ignore-db` — ignores `downloaded` flag in DB; downloads all videos not found on disk. Useful when the DB was seeded with `--register-videos` but files were never actually downloaded.

### Fixed

- **`'Video' object has no attribute 'version'`** (`core/utils/format.py`)
  — `generate_template_data` now uses `getattr(..., None)` for Track-only fields
  (`version`, `copyright`, `bpm`, `isrc`, `track_number`, `volume_number`) so it works
  safely with `Video` objects.

- **Video metadata now uses same MP4 atoms as music tracks** (`core/utils/metadata.py`)
  — `add_video_metadata` now calls `add_m4a_metadata` directly, writing identical atoms
  (`©nam`, `©ART`, `aART`, `©alb`, `©day`, `trkn`, `disk`). Previously used `EasyMP4`
  with a different tag format.

- **HLS video files with invalid MP4 structure** (`core/utils/metadata.py`)
  — When mutagen can't open the file, `fix_mp4_faststart` is called via ffmpeg to remux
  it before retrying metadata write.

- **Video download progress display** (`cmd/download.py`, `core/downloader.py`)
  — Videos now use the same `RichUI` panels as music downloads. HLS segment progress
  shown as `N/Total` in the download bar. Live display runs for the entire session
  (not restarted per video). Total progress bar counts all videos including skips.

- **Unicode logging crash on Windows** (`cli.py`)
  — Log stream handler now forces UTF-8 encoding to prevent `UnicodeEncodeError`
  on cp1252 terminals when log messages contain non-ASCII characters (e.g., `→`).

---

## [1.2.0] — 2026-04-17

### Added

- **Video database tracking** (`core/db.py`, `cmd/download.py`)
  — Downloaded videos are recorded in a new `videos` SQLite table. Already-downloaded
  videos are skipped automatically on future runs.
  - New `videos` table: `video_id`, `title`, `artist_name`, `release_date`,
    `downloaded`, `downloaded_date`. Migrated automatically on first run (Migration 4).
  - `Database.is_video_downloaded(video_id)` — checked before every download.
  - `Database.mark_video_as_downloaded(...)` — called after every successful download.

- **Full video workflow in `tidmon refresh`** (`cmd/refresh.py`, `cli.py`)
  — New flags for complete control over video detection and download:

  | Flag | Effect |
  |---|---|
  | `--download` | Downloads new albums + videos from artists with new releases. |
  | `--download --videos-only` | Downloads only new videos (scans all artists). |
  | `--check-videos` | Detects new videos, shows in summary. No download, no DB write. |
  | `--register-videos` | Detects new videos and adds them to DB as known. No download. Useful for seeding the DB so only future videos are treated as new. |
  | `--video-since YYYY-MM-DD` | Limits video scan to artists added after this date. |
  | `--video-until YYYY-MM-DD` | Limits video scan to artists added before this date. |

- **`tidmon download video <VIDEO_ID>`** (`cli.py`)
  — New CLI subcommand to download a single video by its TIDAL ID. Supports `--force`.

- **Pydantic validation fix** (`core/models/base.py`)
  — `ArtistVideosItems` now skips individual video items that fail validation
  (e.g. videos with incomplete album metadata) instead of discarding the entire page.

---

## [1.1.9] — 2026-03-22

### Added

- **`tidmon reset` command** (`cli.py`)
  — Restores the previously available `reset` command that deletes all monitored
  artists, albums and download history from the database.
  - `tidmon reset` — resets the entire database (artists, albums, playlists) after
    confirmation prompt.
  - `tidmon reset --artists` — removes only monitored artists and their albums.

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
