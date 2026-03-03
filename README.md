# tidmon

> [!WARNING]
> **This app is for personal, educational, and archival purposes only.**
> It is not affiliated with Tidal. Users must ensure their use complies with Tidal's terms of service and all applicable local copyright laws. Downloaded content is for personal use and may not be shared or redistributed. The developer assumes no responsibility for misuse of this app.

A powerful command-line tool for monitoring TIDAL artists, tracking new releases, and automating your music library management.

`tidmon` helps you keep your local music collection perfectly in sync with your favorite artists' discographies on TIDAL. It maintains a local database of artists you want to follow, checks for new albums, and provides a robust downloader to save them to your machine.

## Features

- **Artist & Playlist Monitoring**: Keep a list of your favorite artists and playlists to track for new releases.
- **Automatic Refresh**: Check for new albums and tracks with a single command.
- **High-Quality Downloads**: Download music in the highest quality available, including Hi-Res FLAC (MAX), with fallback to lower qualities.
- **Sequential Downloader**: Each track is fully completed (audio → lyrics → metadata → cover) before moving to the next, just like tiddl.
- **Flexible Downloads**: Download by artist, album, track, or URL. Supports resuming interrupted downloads and forcing re-downloads.
- **Customizable File Organization**: Use powerful and flexible templates to define your folder structure and file naming conventions.
- **Robust and Resilient**: Handles token expiration automatically for long-running sessions and includes rate-limiting to respect the TIDAL API.
- **Local Database**: All monitored items and release history are stored locally, giving you full control over your data.
- **Backup & Restore**: Create and restore backups of your database and configuration at any time.

## Installation

**Prerequisites**:
- **Python 3.10+**
- **FFmpeg**: Must be installed and available in your system's PATH. Required for processing audio and video files.

Install directly from GitHub with a single command:

```bash
pip install git+https://github.com/Np3ir/tidmon-cli.git
```

This will download the project, install all dependencies, and create the `tidmon` command in your system.

## Data Directory

`tidmon` stores your database, configuration, and authentication tokens in:

- **Windows**: `C:\Users\YourUser\AppData\Roaming\tidmon\`
- **Linux/macOS**: `~/.local/share/tidmon/`

This directory is never affected by uninstalling or reinstalling `tidmon`.

## Quick Start

1. **Authenticate with TIDAL**:
    ```bash
    tidmon auth
    ```

2. **Monitor an Artist**:
    ```bash
    tidmon monitor add "Daft Punk"
    tidmon monitor add "https://tidal.com/artist/12345"
    ```

3. **Check for New Releases & Download**:
    ```bash
    tidmon refresh --download
    ```

4. **Download a Specific Album or URL**:
    ```bash
    tidmon download url "https://tidal.com/album/12345"
    ```

## Command Reference

For a full list of all available commands, options, and advanced usage examples, please see the complete guide:

**[--> Full Command Reference (COMMANDS.md)](COMMANDS.md)**

## Configuration

`tidmon` is highly customizable. To learn how to configure download paths, file naming templates, download quality, and more, check out the configuration guide:

**[--> Configuration Guide (CONFIG_GUIDE.md)](CONFIG_GUIDE.md)**