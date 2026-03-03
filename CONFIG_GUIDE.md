# `tidmon` Configuration Guide (`config.json`)

`tidmon` is highly customizable through a `config.json` file located in your user's data directory. This guide explains all the available options.

- **Windows**: `C:\Users\YourUser\AppData\Roaming\tidmon\config.json`
- **Linux/macOS**: `~/.local/share/tidmon/config.json`

The file is created automatically the first time you run `tidmon`. You only need to add the keys you want to change — any key not present will use its default value.

---

## Example `config.json`

Here is a complete `config.json` with all available options and their defaults.

```json
{
  "version": "1.0.0",
  "user_id": null,
  "country_code": "US",
  "check_new_releases": true,
  "record_types": [
    "ALBUM",
    "EP",
    "SINGLE",
    "COMPILATION"
  ],
  "quality_order": [
    "MAX",
    "HI_RES_LOSSLESS",
    "LOSSLESS",
    "HIGH",
    "LOW"
  ],
  "save_cover": true,
  "embed_cover": true,
  "save_lrc": false,
  "save_video": true,
  "download_location": {
    "default": "/path/to/your/music/tidmon",
    "video": "/path/to/your/videos/tidmon"
  },
  "email_notifications": false,
  "smtp_server": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_use_tls": true,
  "email_from": "",
  "email_to": "",
  "email_password": "",
  "debug_mode": false,
  "monitor_interval_hours": 24,
  "templates": {
    "default": "{artist_initials}/{album.artist}/({album.date:%Y-%m-%d}) {album.title} ({album.release})/{item.number}. {item.artists_with_features} - {item.title_version} {item.explicit:shortparens}",
    "video": "{artist_initials}/{album.artist}/({item.releaseDate:%Y-%m-%d}) {item.artists_with_features} - {item.title_version} {item.explicit:shortparens}",
    "playlist": "{playlist.title}/{item.artists_with_features} - {item.title_version} {item.explicit:shortparens}"
  }
}
```

---

## Configuration Options Explained

### General

- `user_id`, `country_code`
  - Your TIDAL user ID and country code. Set automatically during `tidmon auth`.

- `check_new_releases`: `true` | `false`
  - Whether `tidmon` tracks new releases for monitored artists.

- `record_types`: `[string]`
  - Album types to monitor and download. Any type not in this list will be ignored.
  - **Available values**: `"ALBUM"`, `"EP"`, `"SINGLE"`, `"COMPILATION"`.

### Download Quality

- `quality_order`: `[string]`
  - Preferred download qualities in order of preference. `tidmon` tries each in order, falling back to the next if unavailable.
  - **Available values**:
    - `"MAX"` — Hi-Res FLAC / MQA (best quality)
    - `"HI_RES_LOSSLESS"` — Standard Hi-Res FLAC
    - `"LOSSLESS"` — CD Quality FLAC (16-bit, 44.1 kHz)
    - `"HIGH"` — 320 kbps AAC
    - `"LOW"` — 96 kbps AAC

### File and Metadata

- `save_cover`: `true` | `false`
  - Saves the album cover as `cover.jpg` in the album folder.

- `embed_cover`: `true` | `false`
  - Embeds the album cover into the metadata of each audio file.

- `save_lrc`: `true` | `false`
  - Downloads synchronized lyrics in LRC format (if available). Compatible with most music players that support synced lyrics.

- `save_video`: `true` | `false`
  - Downloads music videos when fetching an artist's content.

### Download Paths

- `download_location`: `{...}`
  - Root directories for your downloads.
  - `"default"`: Base path for all audio downloads.
  - `"video"`: Base path for all video downloads.

### Email Notifications

- `email_notifications`: `true` | `false`
  - Receive an email summary when `tidmon refresh` finds new releases.

- `smtp_server`, `smtp_port`, `smtp_use_tls`
  - SMTP server configuration. Defaults are set for Gmail.

- `email_from`, `email_to`, `email_password`
  - Your email credentials. If using Gmail with 2-Factor Authentication, use an **App Password** instead of your regular password.

---

## Path Templating System

The `templates` section controls the exact folder structure and filenames for your downloads.

- `"default"`: Template for standard album track downloads.
- `"video"`: Template for music video downloads.
- `"playlist"`: Template for tracks downloaded from a playlist.

### Available Template Variables

**Track / Item variables:**

| Variable | Description | Example |
|---|---|---|
| `{item.title}` | Track title | `Starboy` |
| `{item.title_version}` | Track version | `Remastered` |
| `{item.artist}` | Main track artist | `The Weeknd` |
| `{item.artists}` | All artists, comma-separated | `The Weeknd, Daft Punk` |
| `{item.artists_with_features}` | Main artist + featured artists | `The Weeknd ft. Daft Punk` |
| `{item.number}` | Track number | `01` |
| `{item.releaseDate}` | Track release date | `2016-11-25` |
| `{item.explicit}` | "Explicit" if explicit | `Explicit` |
| `{item.explicit:short}` | Short explicit marker | `E` |
| `{item.explicit:shortparens}` | Short explicit in parentheses | `(E)` |

**Album variables:**

| Variable | Description | Example |
|---|---|---|
| `{album.title}` | Album title | `Starboy` |
| `{album.artist}` | Main album artist | `The Weeknd` |
| `{album.artists}` | All album artists, comma-separated | `The Weeknd` |
| `{album.date}` | Album release date | `2016-11-25` |
| `{album.year}` | Album release year | `2016` |
| `{album.release}` | Release type | `ALBUM` |
| `{artist_initials}` | First letter of artist name | `T` |

**Playlist variables:**

| Variable | Description |
|---|---|
| `{playlist.title}` | Playlist title |

### Date Formatting

Any date variable supports Python `strftime` directives:

| Format | Output |
|---|---|
| `{album.date:%Y-%m-%d}` | `2016-11-25` |
| `{album.date:%Y}` | `2016` |
| `{album.date:%B %d, %Y}` | `November 25, 2016` |

### Template Examples

**Default (organized by artist initial and year):**
```
{artist_initials}/{album.artist}/({album.date:%Y-%m-%d}) {album.title} ({album.release})/{item.number}. {item.artists_with_features} - {item.title_version} {item.explicit:shortparens}
```
Result: `T/The Weeknd/(2016-11-25) Starboy (ALBUM)/01. The Weeknd ft. Daft Punk - Starboy`

**Simple (artist/year - album/track):**
```
{album.artist}/{album.date:%Y} - {album.title}/{item.number}. {item.title}
```
Result: `The Weeknd/2016 - Starboy/01. Starboy`