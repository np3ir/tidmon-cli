# `tidmon` Command Guide

This is a reference guide for all available commands in `tidmon`.

---

## Main Commands

- `tidmon [OPTIONS] COMMAND [ARGS]...`

**Global Options:**

- `--version`: Show the version of `tidmon`.
- `-v`, `--verbose`: Show detailed information messages (INFO).
- `-d`, `--debug`: Show debugging messages (DEBUG).
- `--help`: Show help for a command.

---

## `auth`: Authentication

Manages the connection and login with your TIDAL account.

- **`tidmon auth`**: Starts the interactive authentication process through the browser.
- **`tidmon logout`**: Deletes the saved authentication credentials.
- **`tidmon whoami`**: Shows information about the current session (user, country, and token expiration time).

---

## `monitor`: Manage Artists and Playlists

The core of `tidmon`. Allows you to add, remove, and list the artists and playlists you want to supervise.

- **`tidmon monitor add [IDENTIFIERS...] [-f FILE]`**: Adds one or more artists or playlists.
  - Accepts artist names (e.g., `"Daft Punk"`), artist IDs, TIDAL artist URLs, and TIDAL playlist URLs.
  - `--file, -f`: Imports a list of artists/playlists from a text file (one per line).

- **`tidmon monitor remove [IDENTIFIERS...]`**: Removes artists from monitoring by name or ID.

- **`tidmon monitor clear`**: Removes **all** artists from monitoring (asks for confirmation).

- **`tidmon monitor export [-o FILE]`**: Exports the list of monitored artists and playlists to a file.

### `monitor playlist`: Playlist-specific Commands

- **`tidmon monitor playlist add <URL>`**: Adds a TIDAL playlist to monitoring and imports all its artists.
- **`tidmon monitor playlist remove <URL>`**: Removes a playlist from monitoring.
- **`tidmon monitor playlist list`**: Lists all monitored playlists.

---

## `refresh`: Search for New Releases

Checks TIDAL for new albums or tracks from the artists and playlists you are monitoring.

- **`tidmon refresh [OPTIONS]`**

**Options:**

- `-D`, `--download`: Automatically downloads the new releases found.
- `--artist <NAME>`, `-a <NAME>`: Refreshes only a specific artist by name.
- `--id <ID>`: Refreshes only a specific artist by ID.
- `--since <YYYY-MM-DD>`: Only refresh artists added *after* this date.
- `--until <YYYY-MM-DD>`: Only refresh artists added *before* this date.
- `--album-since <YYYY-MM-DD>`: Only process albums released *after* this date.
- `--album-until <YYYY-MM-DD>`: Only process albums released *before* this date.
- `--no-artists`: Skips refreshing artists.
- `--no-playlists`: Skips refreshing playlists.

---

## `download`: Download Music and Videos

The advanced download system of `tidmon`. Each track is fully completed (audio → lyrics → metadata → cover) before moving to the next.

- **`tidmon download url <URL>`**: Downloads content from a TIDAL URL (artist, album, track, video, or playlist).
- **`tidmon download artist <ID|NAME>`**: Downloads the complete discography of an artist.
- **`tidmon download album <ALBUM_ID>`**: Downloads a full album by its ID.
- **`tidmon download track <TRACK_ID>`**: Downloads a single track by its ID.
- **`tidmon download monitored`**: Downloads all pending albums from monitored artists.
- **`tidmon download all`**: Downloads **all** albums from the database, regardless of their status.

**Common Download Options:**

- `--force`: Re-downloads content even if the file already exists.
- `--dry-run`: Shows what would be downloaded without performing the actual download.
- `--resume`: Resumes a bulk download (`download all`), skipping already completed albums.
- `--since <DATE>` / `--until <DATE>`: Filters albums to process by their release date.

---

## `show`: Display Database Information

Lets you inspect the local database of artists, albums, and releases.

- **`tidmon show artists`**: Lists all monitored artists.
  - `--playlists`: Show monitored playlists instead.
  - `--all`: Show both artists and playlists.
  - `--csv`: Export the list as a CSV file.
  - `--output, -o <FILE>`: Output file path for the CSV export.

- **`tidmon show releases [OPTIONS]`**: Shows recent or upcoming releases.
  - `--days, -d <N>`: Number of days to look back (default: 30).
  - `--future, -f`: Show upcoming releases instead.
  - `--export <FILE>`: Export to file — `.csv` for spreadsheet, any other extension for tiddl-compatible URL list.

- **`tidmon show albums [OPTIONS]`**: Shows albums from the database with filters.
  - `--artist, -a <NAME|ID>`: Filter by a specific artist.
  - `--pending`: Show only albums not yet downloaded.
  - `--since <DATE>` / `--until <DATE>`: Filter albums by release date.
  - `--export <FILE>`: Export to file — `.csv` for spreadsheet, any other extension for tiddl-compatible URL list.

- **`tidmon show report [OPTIONS]`**: Shows a per-artist summary with album count and total song count.
  - Displays a Rich table in the console with columns: Artist ID, Artist Name, Albums, Songs, and a totals footer row.
  - `--export <FILE>`: Export the report to a file:
    - `.csv` — UTF-8 BOM-encoded CSV (compatible with Excel). Columns: `artist_id`, `artist_name`, `album_count`, `total_tracks`.
    - `.html` — Dark-themed styled HTML table with a totals row.

  **Examples:**
  ```bash
  tidmon show report
  tidmon show report --export reporte_artistas.csv
  tidmon show report --export reporte_artistas.html
  ```

  > **Note:** Artists with `album_count = 0` were added to monitoring but have not been refreshed yet via `tidmon refresh`.

---

## `search`: Search on TIDAL

- **`tidmon search <QUERY> [OPTIONS]`**: Searches TIDAL for artists, albums, or tracks.
  - `--type, -t [artists|albums|tracks]`: Type of content to search (default: `artists`).
  - `--limit, -l <N>`: Maximum number of results to return (default: 10).

---

## `config`: Configuration

Allows you to view and modify the `tidmon` configuration.

- **`tidmon config show`**: Shows the entire current configuration.
- **`tidmon config get <KEY>`**: Gets the value of a specific configuration key.
- **`tidmon config set <KEY> <VALUE>`**: Sets a new value for a configuration key.
- **`tidmon config path`**: Shows the path to the `config.json` file.

**Examples:**

```bash
tidmon config set save_lrc true
tidmon config set monitor_interval_hours 12
tidmon config set quality_order '["MAX", "HI_RES_LOSSLESS", "LOSSLESS"]'
```

---

## `backup`: Backup and Restore

Manages the creation and restoration of backups of your data (database and configuration).

- **`tidmon backup create [-o FILE]`**: Creates a new backup archive. Defaults to the `tidmon` data directory.
- **`tidmon backup restore <FILE>`**: Restores data from a backup file.
- **`tidmon backup list`**: Lists all available backups in the default directory.
- **`tidmon backup delete [<FILE> | --keep <N>]`**: Deletes a specific backup, or all except the N most recent.