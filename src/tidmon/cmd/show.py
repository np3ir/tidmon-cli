import logging
import csv
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.rule import Rule
from rich import box

from tidmon.core.db import Database

logger = logging.getLogger(__name__)
console = Console()


def _export_tiddl(albums: list, path: Path) -> None:
    """Write a .txt file with tiddl download url commands."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"tiddl download url https://tidal.com/album/{a['album_id']}" for a in albums]
    path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]📄 Exported {len(lines)} album(s) to[/] {path}")


def _export_csv(albums: list, path: Path) -> None:
    """Write a .csv file with all album fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["album_id", "artist_name", "title", "album_type", "release_date",
              "number_of_tracks", "downloaded"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(albums)
    console.print(f"[green]📄 Exported {len(albums)} album(s) to[/] {path}")


def _auto_export(albums: list, export_path: str) -> None:
    """Detects format by extension: .csv -> CSV, any other -> tiddl txt."""
    path = Path(export_path)
    if path.suffix.lower() == ".csv":
        _export_csv(albums, path)
    else:
        _export_tiddl(albums, path)


class Show:
    """Display information about artists, releases and albums."""

    def __init__(self):
        self.db = Database()

    # ── Artists ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()

    def __enter__(self) -> "Show":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def show_artists(self, export_csv: bool = False, export_path: str = None,
                     target: str = "artists"):
        """
        Show monitored artists and/or playlists.

        Args:
            export_csv:  Write results to a CSV file instead of printing.
            export_path: Destination path for the CSV file.
            target:      What to display — 'artists', 'playlists', or 'all'.
        """
        if target in ("artists", "all"):
            artists = self.db.get_all_artists()

            if export_csv:
                self._export_artists_csv(artists or [], export_path)
                # Fall through to playlists only if target == 'all'
                if target != "all":
                    return

            if artists:
                counts = self.db.get_album_counts_per_artist()
                table = Table(
                    box=box.SIMPLE_HEAVY,
                    show_header=True,
                    header_style="bold cyan",
                    show_edge=False,
                    pad_edge=False,
                )
                table.add_column("Artist", style="bold", min_width=25)
                table.add_column("ID", style="dim", justify="right")
                table.add_column("Albums", justify="right")
                table.add_column("Added", style="dim")
                table.add_column("Last checked", style="dim")

                for a in artists:
                    album_count = counts.get(a["artist_id"], 0)
                    added   = (a.get("added_date") or "")[:10]
                    checked = (a.get("last_checked") or "Never")[:10]
                    table.add_row(
                        a["artist_name"],
                        str(a["artist_id"]),
                        str(album_count),
                        added,
                        checked,
                    )

                console.print()
                console.print(Rule("[bold]Monitored Artists", style="cyan"))
                console.print(table)
                console.print(f"[dim]Total: {len(artists)} artist(s)[/]\n")
            else:
                console.print("[yellow]No artists being monitored.[/]")

        if target in ("playlists", "all"):
            playlists = self.db.get_monitored_playlists()

            if playlists:
                table = Table(
                    box=box.SIMPLE_HEAVY,
                    show_header=True,
                    header_style="bold cyan",
                    show_edge=False,
                    pad_edge=False,
                )
                table.add_column("Name", style="bold", min_width=30)
                table.add_column("UUID", style="dim")
                table.add_column("Added", style="dim")

                for p in playlists:
                    added = (p.get("added_date") or "")[:10]
                    table.add_row(p.get("name", ""), p.get("uuid", ""), added)

                console.print()
                console.print(Rule("[bold]Monitored Playlists", style="cyan"))
                console.print(table)
                console.print(f"[dim]Total: {len(playlists)} playlist(s)[/]\n")
            else:
                console.print("[yellow]No playlists being monitored.[/]")

    def _export_artists_csv(self, artists: list, export_path: str = None):
        """Export artists to CSV with UTF-8 BOM for Excel compatibility."""
        path = Path(export_path) if export_path else Path.cwd() / "tidmon_artists.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Artist ID", "Artist Name", "Added Date", "Last Checked"])
                for a in artists:
                    writer.writerow([
                        a["artist_id"],
                        a["artist_name"],
                        a.get("added_date", ""),
                        a.get("last_checked", ""),
                    ])
            console.print(f"[green]📄 Exported {len(artists)} artist(s) to[/] {path}")
        except Exception as e:
            logger.error(f"Failed to export CSV: {e}")
            console.print(f"[red]❌ Export failed:[/] {e}")

    # ── Releases ─────────────────────────────────────────────────────────────

    def show_releases(self, days: int = 30, future: bool = False,
                      export: str = None):
        """Show recent or upcoming releases."""
        if future:
            releases = self.db.get_future_releases()
            title    = "Upcoming Releases"
        else:
            releases = self.db.get_recent_releases(days)
            title    = f"Releases — last {days} day(s)"

        if not releases:
            label = "upcoming" if future else "recent"
            console.print(f"[yellow]No {label} releases found.[/]")
            return

        if export:
            _auto_export(releases, export)
            return

        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold cyan",
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Artist", style="bold", min_width=20)
        table.add_column("Title", min_width=25)
        table.add_column("Type", style="dim")
        table.add_column("Released", style="dim")
        table.add_column("Tracks", justify="right", style="dim")
        table.add_column("ID", style="dim", justify="right")

        for r in releases:
            title_cell = r["title"]
            if r.get("explicit"):
                title_cell += " [dim][E][/]"
            table.add_row(
                r["artist_name"],
                title_cell,
                r.get("album_type", "ALBUM"),
                (r.get("release_date") or "")[:10],
                str(r.get("number_of_tracks", "?")),
                str(r["album_id"]),
            )

        console.print()
        console.print(Rule(f"[bold]{title}", style="cyan"))
        console.print(table)
        console.print(f"[dim]Total: {len(releases)} release(s)[/]\n")

    # ── Albums ───────────────────────────────────────────────────────────────

    def show_albums(self, artist: Optional[str] = None, pending: bool = False,
                    since: str = None, until: str = None, export: str = None):
        """Show albums in the database."""
        artist_id = None

        if artist:
            try:
                artist_id = int(artist)
            except ValueError:
                row = self.db.get_artist_by_name(artist)
                if row:
                    artist_id = row["artist_id"]
                else:
                    console.print(f"[red]❌ Artist '{artist}' not found.[/]")
                    return

        albums = self.db.get_albums(
            artist_id=artist_id,
            include_downloaded=not pending,
            since=since,
            until=until,
        )

        if not albums:
            console.print("[yellow]No albums found.[/]")
            return

        # Export mode — write file and return, no table printed
        if export:
            _auto_export(albums, export)
            return

        filter_label = ""
        if artist:
            filter_label = f" — {artist}"
        if pending:
            filter_label += " [pending only]"
        if since or until:
            date_range = f" [{since or ''}→{until or 'now'}]"
            filter_label += date_range

        # Count summary for footer
        total       = len(albums)
        downloaded  = sum(1 for a in albums if a.get("downloaded"))
        pending     = total - downloaded

        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold cyan",
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Artist",   style="bold",      min_width=20)
        table.add_column("Title",                       min_width=25)
        table.add_column("Type",     style="dim",       width=8)
        table.add_column("Released", style="dim",       width=12)
        table.add_column("Tracks",   justify="right",   width=7,  style="dim")
        table.add_column("Explicit", justify="center",  width=4)
        table.add_column("ID",       justify="right",   style="dim")
        table.add_column("Downloaded", justify="center", width=5)

        for a in albums:
            dl_mark      = "[green]✓[/]"  if a.get("downloaded")    else "[dim red]✗[/]"
            explicit_mark = "[yellow]E[/]" if a.get("explicit")       else ""
            tracks        = str(a.get("number_of_tracks") or "?")
            album_type    = (a.get("album_type") or "?").upper()[:7]

            # Color album type for quick scanning
            type_colors = {
                "ALBUM":    "cyan",
                "EP":       "yellow",
                "SINGLE":   "magenta",
                "COMPILAT": "blue",
            }
            color = type_colors.get(album_type, "dim")
            type_cell = f"[{color}]{album_type}[/]"

            table.add_row(
                a["artist_name"],
                a["title"],
                type_cell,
                (a.get("release_date") or "")[:10],
                tracks,
                explicit_mark,
                str(a["album_id"]),
                dl_mark,
            )

        console.print()
        console.print(Rule(f"[bold]Albums{filter_label}", style="cyan"))
        console.print(table)
        console.print(
            f"[dim]Total: {total}  "
            f"[green]Downloaded: {downloaded}[/dim]  "
            f"[dim][yellow]Pending: {pending}[/][/dim]\n"
        )

    # ── Report ───────────────────────────────────────────────────────────────

    def show_report(self, export: str = None) -> None:
        """Show a summary report: release count (all types) and total songs per artist."""
        stats = self.db.get_artist_stats()

        if not stats:
            console.print("[yellow]No artists in database.[/]")
            return

        total_releases = sum(r["releases"] for r in stats)
        total_tracks = sum(r["total_tracks"] for r in stats)

        if export:
            path = Path(export)
            if path.suffix.lower() == ".html":
                self._export_report_html(stats, total_releases, total_tracks, path)
            else:
                self._export_report_csv(stats, path)
            return

        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold cyan",
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Artist", style="bold", min_width=25)
        table.add_column("ID", style="dim", justify="right")
        table.add_column("Releases", justify="right")
        table.add_column("Songs", justify="right")

        for r in stats:
            table.add_row(
                r["artist_name"],
                str(r["artist_id"]),
                str(r["releases"]),
                str(r["total_tracks"]),
            )

        console.print()
        console.print(Rule("[bold]Artist Report — Releases & Songs", style="cyan"))
        console.print(table)
        console.print(
            f"[dim]Total: {len(stats)} artist(s) · "
            f"{total_releases} release(s) · {total_tracks} song(s)[/]\n"
        )

    def _export_report_csv(self, stats: list, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["artist_id", "artist_name", "releases", "total_tracks"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(stats)
        console.print(f"[green]Exported {len(stats)} artist(s) to[/] {path}")

    def _export_report_html(self, stats: list, total_releases: int,
                             total_tracks: int, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows_html = "\n".join(
            f"<tr><td>{r['artist_name']}</td>"
            f"<td class='num'>{r['artist_id']}</td>"
            f"<td class='num'>{r['releases']}</td>"
            f"<td class='num'>{r['total_tracks']}</td></tr>"
            for r in stats
        )
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Tidmon — Artist Report</title>
<style>
  body  {{ font-family: system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0;
           margin: 0; padding: 2rem; }}
  h1   {{ color: #4fc3f7; border-bottom: 2px solid #4fc3f7; padding-bottom: .5rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
  th   {{ text-align: left; color: #4fc3f7; border-bottom: 1px solid #333;
           padding: .4rem .6rem; }}
  td   {{ padding: .3rem .6rem; border-bottom: 1px solid #222; }}
  .num {{ text-align: right; color: #aaa; }}
  tfoot td {{ border-top: 2px solid #4fc3f7; color: #4fc3f7; font-weight: bold; }}
</style>
</head>
<body>
<h1>Artist Report — Releases &amp; Songs</h1>
<table>
  <thead>
    <tr><th>Artist</th><th>ID</th><th>Releases</th><th>Songs</th></tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
  <tfoot>
    <tr>
      <td><strong>TOTAL ({len(stats)} artists)</strong></td>
      <td></td>
      <td class="num">{total_releases}</td>
      <td class="num">{total_tracks}</td>
    </tr>
  </tfoot>
</table>
</body>
</html>"""
        path.write_text(html, encoding="utf-8")
        console.print(f"[green]Exported {len(stats)} artist(s) to[/] {path}")

    # ── Discography ──────────────────────────────────────────────────────────

    def show_discography(self, output_dir: str = ".", formats: list = None):
        """
        Export artist discographies organized into A-Z files.

        For each letter (A-Z) and # (non-alphabetic), generates one file per
        requested format containing all artists starting with that letter and
        their albums, sorted by release date.

        Args:
            output_dir: Directory where the files will be saved.
            formats:    List of formats to generate: 'csv', 'txt', 'html'.
        """
        if formats is None:
            formats = ["csv"]

        # Fetch all data
        artists = self.db.get_all_artists() or []
        if not artists:
            console.print("[yellow]No artists in database.[/]")
            return

        all_albums = self.db.get_albums(include_downloaded=True) or []

        # Group albums by artist_id for fast lookup
        albums_by_artist: dict = {}
        for album in all_albums:
            aid = album["artist_id"] if "artist_id" in album else None
            if aid is None:
                # fallback: match by name
                for a in artists:
                    if a["artist_name"] == album.get("artist_name"):
                        aid = a["artist_id"]
                        break
            if aid is not None:
                albums_by_artist.setdefault(aid, []).append(album)

        # Sort albums within each artist by release date
        for aid in albums_by_artist:
            albums_by_artist[aid].sort(key=lambda x: x.get("release_date") or "")

        # Group artists by first letter
        letters: dict = {}
        for artist in sorted(artists, key=lambda x: x["artist_name"].upper()):
            first = artist["artist_name"][0].upper()
            key = first if first.isalpha() else "#"
            letters.setdefault(key, []).append(artist)

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        total_files = 0
        for letter, letter_artists in sorted(letters.items()):
            if "csv" in formats:
                self._write_discography_csv(letter, letter_artists, albums_by_artist, out)
                total_files += 1
            if "txt" in formats:
                self._write_discography_txt(letter, letter_artists, albums_by_artist, out)
                total_files += 1
            if "html" in formats:
                self._write_discography_html(letter, letter_artists, albums_by_artist, out)
                total_files += 1

        fmt_str = ", ".join(f.upper() for f in formats)
        console.print(
            f"\n[green]OK Discography exported[/] - "
            f"{len(letters)} letter(s) x {len(formats)} format(s) = "
            f"[bold]{total_files} file(s)[/] in [dim]{out}[/]\n"
        )

    # ── Discography writers ──────────────────────────────────────────────────

    def _write_discography_csv(self, letter: str, artists: list,
                                albums_by_artist: dict, out: Path) -> None:
        """Write one CSV file per letter with artist+album rows."""
        path = out / f"{letter}.csv"
        fields = ["artist_name", "artist_id", "album_title", "album_type",
                  "release_date", "number_of_tracks", "downloaded", "album_id"]
        rows = []
        for artist in artists:
            aid = artist["artist_id"]
            for album in albums_by_artist.get(aid, []):
                rows.append({
                    "artist_name":     artist["artist_name"],
                    "artist_id":       aid,
                    "album_title":     album.get("title", ""),
                    "album_type":      album.get("album_type", ""),
                    "release_date":    (album.get("release_date") or "")[:10],
                    "number_of_tracks": album.get("number_of_tracks", ""),
                    "downloaded":      album.get("downloaded", False),
                    "album_id":        album.get("album_id", ""),
                })

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def _write_discography_txt(self, letter: str, artists: list,
                                albums_by_artist: dict, out: Path) -> None:
        """Write one readable TXT file per letter."""
        path = out / f"{letter}.txt"
        lines = [f"{'='*60}", f"  {letter} — Discography", f"{'='*60}\n"]
        for artist in artists:
            aid = artist["artist_id"]
            a_albums = albums_by_artist.get(aid, [])
            lines.append(f"▶ {artist['artist_name']}  (ID: {aid})")
            if a_albums:
                for album in a_albums:
                    date     = (album.get("release_date") or "????-??-??")[:10]
                    a_type   = (album.get("album_type") or "ALBUM")[:1]  # A/E/S/C
                    dl_mark  = "✓" if album.get("downloaded") else "·"
                    tracks   = album.get("number_of_tracks") or "?"
                    lines.append(
                        f"  {dl_mark} [{date}] [{a_type}] "
                        f"{album.get('title', '?')}  ({tracks} tracks)"
                    )
            else:
                lines.append("  (no albums in database)")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")

    def _write_discography_html(self, letter: str, artists: list,
                                 albums_by_artist: dict, out: Path) -> None:
        """Write one styled HTML file per letter."""
        path = out / f"{letter}.html"

        type_colors = {
            "ALBUM":       "#4fc3f7",
            "EP":          "#fff176",
            "SINGLE":      "#f48fb1",
            "COMPILATION": "#a5d6a7",
        }

        artist_blocks = []
        for artist in artists:
            aid = artist["artist_id"]
            a_albums = albums_by_artist.get(aid, [])
            rows = []
            for album in a_albums:
                date    = (album.get("release_date") or "")[:10]
                a_type  = (album.get("album_type") or "ALBUM").upper()
                color   = type_colors.get(a_type, "#ccc")
                dl      = "✓" if album.get("downloaded") else "·"
                dl_cls  = "dl-yes" if album.get("downloaded") else "dl-no"
                tracks  = album.get("number_of_tracks") or "?"
                rows.append(
                    f'<tr>'
                    f'<td class="{dl_cls}">{dl}</td>'
                    f'<td class="date">{date}</td>'
                    f'<td><span class="badge" style="background:{color}">{a_type}</span></td>'
                    f'<td>{album.get("title","")}</td>'
                    f'<td class="tracks">{tracks}</td>'
                    f'<td class="aid">{album.get("album_id","")}</td>'
                    f'</tr>'
                )
            rows_html = "\n".join(rows) if rows else '<tr><td colspan="6" class="empty">No albums</td></tr>'
            count = len(a_albums)
            artist_blocks.append(f"""
    <section class="artist">
      <h2>{artist['artist_name']} <span class="meta">ID {aid} · {count} album(s)</span></h2>
      <table>
        <thead><tr><th></th><th>Date</th><th>Type</th><th>Title</th><th>Tracks</th><th>ID</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </section>""")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Discography — {letter}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 2rem; }}
  h1   {{ color: #4fc3f7; border-bottom: 2px solid #4fc3f7; padding-bottom: .5rem; }}
  h2   {{ color: #fff; margin: 2rem 0 .5rem; font-size: 1.2rem; }}
  .meta {{ color: #888; font-size: .85rem; font-weight: normal; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 1rem; font-size: .9rem; }}
  th   {{ text-align: left; color: #4fc3f7; border-bottom: 1px solid #333; padding: .4rem .6rem; }}
  td   {{ padding: .3rem .6rem; border-bottom: 1px solid #222; }}
  .dl-yes {{ color: #4caf50; font-weight: bold; }}
  .dl-no  {{ color: #555; }}
  .date   {{ color: #aaa; white-space: nowrap; }}
  .tracks {{ text-align: right; color: #aaa; }}
  .aid    {{ text-align: right; color: #555; font-size: .8rem; }}
  .badge  {{ padding: .1rem .45rem; border-radius: 4px; font-size: .75rem;
             font-weight: bold; color: #000; }}
  .empty  {{ color: #555; font-style: italic; }}
  .artist {{ margin-bottom: 2.5rem; }}
</style>
</head>
<body>
<h1>Discography — {letter}</h1>
{''.join(artist_blocks)}
</body>
</html>"""
        path.write_text(html, encoding="utf-8")