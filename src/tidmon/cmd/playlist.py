import logging
import unicodedata
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from tidmon.core.auth import get_session
from tidmon.core.utils.url import parse_url, TidalType
from tidmon.core.models.resources import Track

logger = logging.getLogger(__name__)
console = Console()


def _nfc(text: str) -> str:
    """Compose decomposed Unicode (e.g. combining accents from TIDAL metadata)
    so the Windows legacy console's cp1252 writer can encode it.
    """
    return unicodedata.normalize("NFC", text) if text else text


def _print_safe(renderable) -> None:
    """Print via Rich, falling back to an ASCII-safe render if the Windows
    console can't encode a character NFC normalization didn't resolve
    (e.g. CJK/Cyrillic titles) — the command must not crash mid-run.
    """
    try:
        console.print(renderable)
    except UnicodeEncodeError:
        safe_console = Console(file=console.file, safe_box=True)
        with safe_console.capture() as capture:
            safe_console.print(renderable)
        encoding = getattr(console.file, "encoding", None) or "ascii"
        console.file.write(capture.get().encode(encoding, errors="replace").decode(encoding))


def _export_tiddl(albums: list, path: Path) -> None:
    """Write a .txt file with one 'tiddl download url' command per unique album.

    One command per line, nothing else — LAUNCHER.BAT-style runners read every
    line as a command with no comment/blank-line filtering.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"tiddl download url https://tidal.com/album/{a['album_id']}" for a in albums]
    path.write_text("\n".join(lines), encoding="utf-8")
    _print_safe(f"[green]Exported {len(lines)} album(s) to[/] {path}")


class Playlist:
    """Inspect a Tidal playlist's albums without adding it to monitoring."""

    def __init__(self, session=None):
        self.session = session or get_session()
        self.api = self.session.get_api()

    def albums(self, url_or_uuid: str, export: Optional[str] = None) -> None:
        parsed = parse_url(url_or_uuid)
        if parsed and parsed.tidal_type == TidalType.PLAYLIST:
            playlist_uuid = parsed.tidal_id
        else:
            playlist_uuid = url_or_uuid.strip()

        playlist = self.api.get_playlist(playlist_uuid)
        playlist_title = _nfc(playlist.title) if playlist else playlist_uuid

        _print_safe(f"[cyan]Fetching tracks for playlist:[/] {playlist_title}")
        tracks = self.api.get_playlist_items(playlist_uuid)

        seen_ids = set()
        albums = []
        video_count = 0
        for t in tracks:
            if not isinstance(t, Track):
                video_count += 1
                continue
            if t.album is None:
                continue
            if t.album.id in seen_ids:
                continue
            seen_ids.add(t.album.id)
            artist_name = ""
            if t.album.artist is not None:
                artist_name = t.album.artist.name
            elif t.artist is not None:
                artist_name = t.artist.name
            year = t.album.release_date.year if t.album.release_date else None
            albums.append({
                "album_id": t.album.id,
                "title": _nfc(t.album.title),
                "artist_name": _nfc(artist_name),
                "year": year,
            })

        # Sort oldest-first for readability (matches the "oldest-first" download
        # order convention used elsewhere) — not load-bearing, just display order.
        albums.sort(key=lambda a: (a["year"] or 9999, a["artist_name"].lower()))

        table = Table(title=f"Albums in playlist: {playlist_title}", show_lines=False)
        table.add_column("Year", style="dim", width=6)
        table.add_column("Artist", style="cyan")
        table.add_column("Album", style="white")
        table.add_column("ID", style="dim")
        for a in albums:
            table.add_row(str(a["year"] or "?"), a["artist_name"], a["title"], str(a["album_id"]))
        _print_safe(table)

        summary = f"\n[bold]Tracks:[/] {len(tracks)}   [bold]Unique albums:[/] {len(albums)}"
        if video_count:
            summary += f"   [dim]({video_count} video item(s) skipped, no album)[/]"
        _print_safe(summary)

        if export:
            _export_tiddl(albums, Path(export))
