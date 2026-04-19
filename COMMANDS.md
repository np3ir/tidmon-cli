# `tidmon` Command Guide

Complete reference for all available commands in `tidmon`, including every option and combination.

---

## Global Options

```
tidmon [OPTIONS] COMMAND [ARGS]...
```

| Option | Description |
|---|---|
| `--version` | Show the installed version of `tidmon`. |
| `-v`, `--verbose` | Show detailed info messages (INFO level). |
| `-d`, `--debug` | Show full debug messages (DEBUG level). |
| `--help` | Show help for any command or subcommand. |

---

## `auth` — Authentication

| Command | Description |
|---|---|
| `tidmon auth` | Start the interactive browser-based authentication flow. |
| `tidmon logout` | Delete stored authentication credentials. |
| `tidmon whoami` | Show current session info (user, country, token expiration). |
| `tidmon auth-refresh` | Manually refresh the TIDAL access token. |
| `tidmon auth-refresh --force` | Refresh even if the token is still valid. |
| `tidmon auth-refresh --early-expire SECONDS` | Treat token as expired N seconds before actual expiry. |

---

## `monitor` — Manage Artists and Playlists

### Artists

| Command | Description |
|---|---|
| `tidmon monitor add "Artist Name"` | Add an artist by name. |
| `tidmon monitor add 3528531` | Add an artist by TIDAL ID. |
| `tidmon monitor add "https://tidal.com/browse/artist/..."` | Add an artist by URL. |
| `tidmon monitor add "Artist 1" "Artist 2" "Artist 3"` | Add multiple artists at once. |
| `tidmon monitor add --file artists.txt` | Import artists from a text file (one per line). |
| `tidmon monitor add "https://tidal.com/browse/playlist/..."` | Add a playlist and import all its artists. |
| `tidmon monitor remove "Artist Name"` | Remove an artist by name. |
| `tidmon monitor remove 3528531` | Remove an artist by ID. |
| `tidmon monitor clear` | Remove all monitored artists (requires confirmation). |
| `tidmon monitor export` | Export monitored artists to `tidmon_export.txt`. |
| `tidmon monitor export --output my_list.txt` | Export to a specific file. |

### Playlists

| Command | Description |
|---|---|
| `tidmon monitor playlist add <URL>` | Add a TIDAL playlist to monitoring. |
| `tidmon monitor playlist remove <URL>` | Remove a playlist from monitoring. |
| `tidmon monitor playlist list` | List all monitored playlists. |

---

## `refresh` — Check for New Releases and Videos

### All options

| Option | Description |
|---|---|
| `--artist`, `-a NAME` | Refresh only a specific artist by name. |
| `--id ID` | Refresh only a specific artist by ID. |
| `--no-artists` | Skip album refresh entirely. |
| `--no-playlists` | Skip playlist refresh. |
| `--since YYYY-MM-DD` | Only refresh artists added after this date. |
| `--until YYYY-MM-DD` | Only refresh artists added before this date. |
| `--album-since YYYY-MM-DD` | Only process albums released after this date. |
| `--album-until YYYY-MM-DD` | Only process albums released before this date. |
| `--download`, `-D` | Auto-download new content after refresh. |
| `--videos-only` | With `--download`: download only new videos, skip album downloads. |
| `--check-videos` | Detect new videos for all artists and show in summary. No download, no DB write. |
| `--register-videos` | Detect new videos and register them in the DB as known. No download. |
| `--video-since YYYY-MM-DD` | With video flags: only check artists added after this date. |
| `--video-until YYYY-MM-DD` | With video flags: only check artists added before this date. |

### Combinations — Ver sin descargar

| Comando | Qué hace |
|---|---|
| `tidmon refresh` | Detecta álbumes nuevos. Muestra summary. No descarga nada. |
| `tidmon refresh --check-videos` | Detecta álbumes + videos nuevos. Muestra summary. No descarga nada. |
| `tidmon refresh --check-videos --no-artists --no-playlists` | Solo detecta videos nuevos. Muestra summary. No descarga nada. |
| `tidmon refresh --check-videos --artist "Bad Bunny"` | Detecta álbumes + videos de un artista específico. |
| `tidmon refresh --check-videos --video-since 2026-01-01` | Detecta álbumes + videos de artistas añadidos desde esa fecha. |
| `tidmon refresh --check-videos --no-artists --no-playlists --video-since 2026-01-01` | Solo detecta videos de artistas añadidos desde esa fecha. |

### Combinations — Registrar en DB sin descargar

| Comando | Qué hace |
|---|---|
| `tidmon refresh --register-videos` | Detecta álbumes + registra todos los videos en DB (como conocidos). No descarga. |
| `tidmon refresh --register-videos --no-artists --no-playlists` | Solo registra videos en DB. Ideal para seed inicial. |
| `tidmon refresh --register-videos --video-since 2026-01-01` | Registra videos de artistas añadidos desde esa fecha. |
| `tidmon refresh --register-videos --no-artists --no-playlists --video-since 2025-01-01 --video-until 2025-12-31` | Registra videos de artistas añadidos durante 2025. |

### Combinations — Descargar álbumes

| Comando | Qué hace |
|---|---|
| `tidmon refresh --download` | Detecta + descarga álbumes nuevos. También descarga videos de artistas con releases nuevas. |
| `tidmon refresh --download --album-since 2026-01-01` | Descarga solo álbumes publicados desde esa fecha. |
| `tidmon refresh --download --since 2026-01-01` | Descarga álbumes de artistas añadidos a la monitorización desde esa fecha. |
| `tidmon refresh --download --artist "Bad Bunny"` | Descarga álbumes nuevos de un artista específico. |
| `tidmon refresh --download --id 3528531` | Descarga álbumes nuevos de un artista por ID. |
| `tidmon refresh --download --no-playlists` | Descarga álbumes nuevos, ignora playlists. |

### Combinations — Descargar videos

| Comando | Qué hace |
|---|---|
| `tidmon refresh --download --videos-only` | Descarga videos nuevos de todos los artistas. No descarga álbumes. |
| `tidmon refresh --download --videos-only --no-artists --no-playlists` | Solo descarga videos (sin escanear álbumes). Más rápido. |
| `tidmon refresh --download --videos-only --no-artists --no-playlists --video-since 2026-01-01` | Solo descarga videos de artistas añadidos desde esa fecha. |
| `tidmon refresh --download --videos-only --artist "Bad Bunny"` | Descarga videos nuevos de un artista específico. |
| `tidmon refresh --download --videos-only --video-since 2025-01-01 --video-until 2025-12-31` | Descarga videos de artistas añadidos durante 2025. |

### Combinations — Descargar todo (álbumes + videos)

| Comando | Qué hace |
|---|---|
| `tidmon refresh --download` | Descarga álbumes nuevos + videos de artistas con releases nuevas. |
| `tidmon refresh --download --check-videos` | Descarga álbumes nuevos + videos de **todos** los artistas. |
| `tidmon refresh --download --check-videos --album-since 2026-01-01` | Lo mismo, filtrando álbumes por fecha de publicación. |
| `tidmon refresh --download --check-videos --video-since 2026-01-01` | Descarga álbumes de todos + videos de artistas añadidos desde esa fecha. |

### Flujo recomendado — primer uso con muchos artistas

```bash
# 1. Sembrar la DB con todos los videos existentes (no descarga nada, puede tardar)
tidmon refresh --register-videos --no-artists --no-playlists

# 2. A partir de ahora, detectar y descargar solo los videos nuevos
tidmon refresh --download --videos-only --no-artists --no-playlists

# 3. Para un refresh completo (álbumes + videos nuevos)
tidmon refresh --download
```

---

## `download` — Download Music and Videos

### `download url` — Por URL

| Comando | Qué hace |
|---|---|
| `tidmon download url "https://tidal.com/browse/album/123456"` | Descarga un álbum completo. |
| `tidmon download url "https://tidal.com/browse/track/123456"` | Descarga un track. |
| `tidmon download url "https://tidal.com/browse/video/123456"` | Descarga un video. |
| `tidmon download url "https://tidal.com/browse/artist/123456"` | Descarga discografía completa + videos del artista. |
| `tidmon download url "https://tidal.com/browse/playlist/..."` | Descarga todos los tracks de una playlist. |
| `tidmon download url <URL> --force` | Re-descarga aunque ya exista en disco o en DB. |

### `download artist` — Por artista

| Comando | Qué hace |
|---|---|
| `tidmon download artist "Rosalía"` | Descarga discografía completa + videos por nombre. |
| `tidmon download artist 3528531` | Descarga discografía completa + videos por ID. |
| `tidmon download artist "Rosalía" --force` | Re-descarga todo aunque ya exista. |

### `download album` — Por álbum

| Comando | Qué hace |
|---|---|
| `tidmon download album 123456` | Descarga un álbum por ID. |
| `tidmon download album 123456 --force` | Re-descarga aunque ya exista. |

### `download track` — Por track

| Comando | Qué hace |
|---|---|
| `tidmon download track 123456` | Descarga un track por ID. |
| `tidmon download track 123456 --force` | Re-descarga aunque ya exista. |

### `download video` — Por video

| Comando | Qué hace |
|---|---|
| `tidmon download video 123456` | Descarga un video por ID. |
| `tidmon download video 123456 --force` | Re-descarga aunque ya esté en DB. |

### `download pending-videos` — Videos pendientes de la DB

| Comando | Qué hace |
|---|---|
| `tidmon download pending-videos` | Descarga todos los videos en DB con `downloaded=0`. |
| `tidmon download pending-videos --dry-run` | Muestra los videos pendientes sin descargar. |
| `tidmon download pending-videos --force` | Re-descarga aunque el archivo ya exista en disco. |
| `tidmon download pending-videos --ignore-db` | Ignora el estado de la DB — descarga todos los videos que no existan en disco. Útil si la DB fue sembrada con `--register-videos` pero los archivos no fueron descargados. |

### `download playlist` — Tracks de una playlist

| Comando | Qué hace |
|---|---|
| `tidmon download playlist "https://tidal.com/browse/playlist/..."` | Descarga todos los tracks de la playlist. |
| `tidmon download playlist <URL> --force` | Re-descarga aunque ya existan en disco. |

> Los tracks se guardan usando el template `playlist` (por defecto: `{playlist.title}/{item.artists_with_features} - {item.title_version}`).
> Configurable con `tidmon config set templates.playlist "..."`.

### `download monitored` — Álbumes pendientes

| Comando | Qué hace |
|---|---|
| `tidmon download monitored` | Descarga todos los álbumes pendientes (no marcados como descargados). |
| `tidmon download monitored --since 2026-01-01` | Solo álbumes publicados desde esa fecha. |
| `tidmon download monitored --until 2026-12-31` | Solo álbumes publicados antes de esa fecha. |
| `tidmon download monitored --since 2026-01-01 --until 2026-03-31` | Solo álbumes en ese rango de fechas. |
| `tidmon download monitored --dry-run` | Muestra qué se descargaría sin descargar nada. |
| `tidmon download monitored --force` | Re-descarga aunque ya estén marcados como descargados. |

### `download all` — Todos los álbumes

| Comando | Qué hace |
|---|---|
| `tidmon download all` | Descarga todos los álbumes de la DB. |
| `tidmon download all --resume` | Salta los álbumes ya descargados. |
| `tidmon download all --force` | Re-descarga todo ignorando estado en DB y disco. |
| `tidmon download all --dry-run` | Muestra qué se descargaría sin descargar nada. |
| `tidmon download all --since 2026-01-01` | Solo álbumes publicados desde esa fecha. |
| `tidmon download all --until 2026-12-31` | Solo álbumes publicados antes de esa fecha. |
| `tidmon download all --since 2026-01-01 --resume` | Desde esa fecha, saltando los ya descargados. |

---

## `show` — Inspect the Database

### `show artists`

| Comando | Qué hace |
|---|---|
| `tidmon show artists` | Lista todos los artistas monitoreados. |
| `tidmon show artists --playlists` | Lista las playlists monitoreadas. |
| `tidmon show artists --all` | Lista artistas y playlists. |
| `tidmon show artists --csv` | Exporta artistas a CSV (archivo por defecto). |
| `tidmon show artists --csv --output lista.csv` | Exporta artistas a un CSV específico. |

### `show releases`

| Comando | Qué hace |
|---|---|
| `tidmon show releases` | Muestra releases de los últimos 30 días. |
| `tidmon show releases --days 7` | Muestra releases de los últimos 7 días. |
| `tidmon show releases --future` | Muestra próximas releases. |
| `tidmon show releases --export releases.csv` | Exporta a CSV. |
| `tidmon show releases --export releases.txt` | Exporta como lista de comandos tiddl. |

### `show albums`

| Comando | Qué hace |
|---|---|
| `tidmon show albums` | Lista todos los álbumes en la DB. |
| `tidmon show albums --artist "Rosalía"` | Solo álbumes de ese artista. |
| `tidmon show albums --artist 3528531` | Solo álbumes de ese artista por ID. |
| `tidmon show albums --pending` | Solo álbumes aún no descargados. |
| `tidmon show albums --pending --artist "Rosalía"` | Álbumes pendientes de un artista. |
| `tidmon show albums --since 2026-01-01` | Solo álbumes publicados desde esa fecha. |
| `tidmon show albums --until 2026-12-31` | Solo álbumes publicados antes de esa fecha. |
| `tidmon show albums --since 2026-01-01 --until 2026-03-31` | Álbumes en ese rango. |
| `tidmon show albums --export albums.csv` | Exporta a CSV. |
| `tidmon show albums --export albums.txt` | Exporta como lista de comandos tiddl. |
| `tidmon show albums --pending --export pending.txt` | Exporta pendientes como comandos tiddl. |

### `show report`

| Comando | Qué hace |
|---|---|
| `tidmon show report` | Tabla por artista: álbumes y canciones totales. |
| `tidmon show report --export report.csv` | Exporta a CSV (compatible con Excel). |
| `tidmon show report --export report.html` | Exporta a HTML con tabla oscura. |

### `show discography`

| Comando | Qué hace |
|---|---|
| `tidmon show discography` | Genera archivos A-Z en CSV, TXT y HTML en la carpeta actual. |
| `tidmon show discography --format csv` | Solo en formato CSV. |
| `tidmon show discography --format csv --format html` | En CSV y HTML. |
| `tidmon show discography --output ~/Music/catalog` | Guarda en esa carpeta. |
| `tidmon show discography --format txt --output ~/Music/catalog` | TXT en esa carpeta. |

---

## `search` — Search TIDAL

| Comando | Qué hace |
|---|---|
| `tidmon search "Rosalía"` | Busca artistas con ese nombre (top 10). |
| `tidmon search "Rosalía" --limit 5` | Busca artistas, muestra solo 5 resultados. |
| `tidmon search "Motomami" --type albums` | Busca álbumes. |
| `tidmon search "Con Altura" --type tracks` | Busca tracks. |
| `tidmon search "Con Altura" --type tracks --limit 3` | Busca tracks, muestra 3 resultados. |

---

## `config` — Configuration

| Comando | Qué hace |
|---|---|
| `tidmon config show` | Muestra toda la configuración actual. |
| `tidmon config path` | Muestra la ruta del archivo `config.json`. |
| `tidmon config get save_video` | Obtiene el valor de una clave. |
| `tidmon config set save_video true` | Activa la descarga de videos. |
| `tidmon config set save_lrc true` | Activa la descarga de letras sincronizadas. |
| `tidmon config set save_cover true` | Guarda la carátula como `cover.jpg`. |
| `tidmon config set embed_cover true` | Incrusta la carátula en los archivos de audio. |
| `tidmon config set quality_order '["MAX","HI_RES_LOSSLESS","LOSSLESS"]'` | Cambia el orden de calidades. |
| `tidmon config set artist_separator " / "` | Cambia el separador de artistas. |
| `tidmon config set monitor_interval_hours 12` | Cambia el intervalo de monitorización. |
| `tidmon config set concurrent_downloads 4` | Cambia las descargas simultáneas. |

---

## `backup` — Backup and Restore

| Comando | Qué hace |
|---|---|
| `tidmon backup create` | Crea un backup en la carpeta de datos de tidmon. |
| `tidmon backup create --output /ruta/backup.zip` | Crea un backup en una ruta específica. |
| `tidmon backup restore backup.zip` | Restaura desde un backup. |
| `tidmon backup list` | Lista todos los backups disponibles. |
| `tidmon backup delete backup.zip` | Elimina un backup específico. |
| `tidmon backup delete --keep 3` | Mantiene solo los 3 backups más recientes. |

---

## `reset` — Reset the Database

| Comando | Qué hace |
|---|---|
| `tidmon reset` | Elimina toda la DB (artistas, álbumes, playlists). Pide confirmación. |
| `tidmon reset --artists` | Elimina solo artistas y sus álbumes. |
| `tidmon reset --db` | Igual que sin opciones — resetea la DB completa. |

> ⚠️ Irreversible. Usa `tidmon backup create` antes de resetear.
