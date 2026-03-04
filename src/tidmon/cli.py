"""
tidmon - TIDAL Release Monitor
CLI entry point (mirroring deemon's structure for TIDAL)
"""
import logging
import click
from tidmon.core.config import Config
from tidmon.core.auth import get_session
from pathlib import Path
from tidmon.cmd.auth import Auth
from tidmon.cmd.monitor import Monitor
from tidmon.cmd.refresh import Refresh
from tidmon.cmd.download import Download
from tidmon.cmd.search import Search
from tidmon.cmd.show import Show
from tidmon.cmd.config import ConfigCommand
from tidmon.cmd.backup import Backup
from tidmon.core.utils.url import parse_url, TidalType

# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False, debug: bool = False):
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s" if debug else "%(levelname)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)

    # File handler for errors (optional)
    try:
        log_dir = Path.home() / ".tidmon" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "tidmon.log")
        file_handler.setLevel(logging.WARNING)  # Log warnings and errors to file
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(file_handler)
    except Exception:
        # Logging to file is optional; do not crash if it fails (e.g., due to permissions)
        pass


# ── Root command ──────────────────────────────────────────────────────────────

@click.group()
@click.version_option("1.1.1", prog_name="tidmon")
@click.option('--verbose', '-v', is_flag=True, help='Show info messages.')
@click.option('--debug', '-d', is_flag=True, help='Show debug messages.')
@click.pass_context
def cli(ctx, verbose, debug):
    """tidmon — TIDAL Release Monitor\n\nTrack new releases from your favourite artists."""
    setup_logging(verbose, debug)
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    ctx.obj['debug'] = debug
    ctx.obj['config']  = Config()
    ctx.obj['session'] = get_session()


# ── auth ──────────────────────────────────────────────────────────────────────

@cli.command()
def auth():
    """Authenticate with TIDAL using device flow."""
    Auth().login()


@cli.command()
def logout():
    """Clear stored authentication tokens."""
    Auth().logout()


@cli.command()
def whoami():
    """Show current authentication status."""
    Auth().status()


@cli.command('auth-refresh')
@click.option('--force', '-f', is_flag=True, help='Refresh even if token is still valid.')
@click.option('--early-expire', '-e', default=0, metavar='seconds',
              help='Treat token as expired this many seconds before actual expiry.')
def auth_refresh(force, early_expire):
    """Refresh the TIDAL access token."""
    Auth().refresh(force=force, early_expire=early_expire)


# ── monitor ───────────────────────────────────────────────────────────────────

@cli.group()
def monitor():
    """Monitor artists and playlists for new releases."""
    pass


@monitor.command('add')
@click.argument('identifiers', nargs=-1, required=False)
@click.option('--file', '-f', 'from_file', type=click.Path(exists=True),
              help='Import from a file (artists or playlists, one per line).')
@click.pass_context
def monitor_add(ctx, identifiers, from_file):
    """Add artist(s) or playlist(s) to monitoring.

    Can accept artist names, IDs, artist URLs, or playlist URLs.
    When a playlist is added, all its artists are monitored.

    \b
    Examples:
      tidmon monitor add "Radiohead"
      tidmon monitor add 3528531
      tidmon monitor add https://tidal.com/browse/artist/3528531
      tidmon monitor add https://tidal.com/browse/playlist/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
      tidmon monitor add --file my_artists.txt
    """
    if not identifiers and not from_file:
        click.echo(click.get_current_context().get_help(), err=True)
        return

    with Monitor(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as m:
        if from_file:
            m.add_from_file(from_file)

        if identifiers:
            for identifier in identifiers:
                parsed = parse_url(identifier)
                if parsed:
                    if parsed.tidal_type == TidalType.ARTIST:
                        m.add_by_id(int(parsed.tidal_id))
                    elif parsed.tidal_type == TidalType.PLAYLIST:
                        m.add_playlist(identifier)
                else:
                    try:
                        m.add_by_id(int(identifier))
                    except ValueError:
                        m.add_by_name(identifier)


@monitor.command('remove')
@click.argument('identifiers', nargs=-1, required=True)
@click.pass_context
def monitor_remove(ctx, identifiers):
    """Remove artist(s) from monitoring."""
    with Monitor(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as m:
        for identifier in identifiers:
            m.remove_artist(identifier)

@monitor.command('clear')
@click.confirmation_option(prompt='Are you sure you want to remove all monitored artists?')
@click.pass_context
def monitor_clear(ctx):
    """Remove all monitored artists."""
    with Monitor(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as m:
        m.clear_artists()





@monitor.group('playlist')
def monitor_playlist():
    """Monitor playlists for new tracks."""
    pass


@monitor_playlist.command('add')
@click.argument('url')
@click.pass_context
def playlist_add(ctx, url):
    """Add a playlist to monitoring."""
    with Monitor(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as m:
        m.add_playlist(url)


@monitor_playlist.command('remove')
@click.argument('url')
@click.pass_context
def playlist_remove(ctx, url):
    """Remove a playlist from monitoring."""
    with Monitor(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as m:
        m.remove_playlist(url)


@monitor_playlist.command('list')
@click.pass_context
def playlist_list(ctx):
    """List all monitored playlists."""
    with Monitor(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as m:
        m.list_playlists()


@monitor.command('export')
@click.option('--output', '-o', default='tidmon_export.txt', show_default=True,
              help='Output file path.')
@click.pass_context
def monitor_export(ctx, output):
    """Export monitored artists and playlists to a file."""
    with Monitor(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as m:
        m.export_to_file(output)


# ── refresh ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option('--artist', '-a', default=None, help='Refresh a specific artist by name.')
@click.option('--id', 'artist_id', default=None, type=int, help='Refresh a specific artist by ID.')
@click.option('--no-artists', 'skip_artists', is_flag=True, help='Skip artist refresh.')
@click.option('--no-playlists', 'skip_playlists', is_flag=True, help='Skip playlist refresh.')
@click.option('--download', '-D', is_flag=True, help='Auto-download new releases after refresh.')
@click.option('--since', default=None, help='Only refresh artists added since date (YYYY-MM-DD).')
@click.option('--until', default=None, help='Only refresh artists added until date (YYYY-MM-DD).')
@click.option('--album-since', default=None, help='Only process albums released after this date (YYYY-MM-DD).')
@click.option('--album-until', default=None, help='Only process albums released before this date (YYYY-MM-DD).')
@click.pass_context
def refresh(ctx, artist, artist_id, skip_artists, skip_playlists, download, since, until, album_since, album_until):
    """Check monitored artists for new releases."""
    with Refresh(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as r:
        r.refresh(
            artist=artist,
            artist_id=artist_id,
            refresh_artists=not skip_artists,
            refresh_playlists=not skip_playlists,
            download=download,
            since=since,
            until=until,
            album_since=album_since,
            album_until=album_until,
        )


# ── download ──────────────────────────────────────────────────────────────────

@cli.group()
def download():
    """Download tracks, albums, artists, or playlists."""
    pass


@download.command('url')
@click.pass_context
@click.argument('url')
@click.option('--force', is_flag=True, default=False, help='Force re-download even if file exists.')
def download_url(ctx, url, force):
    """Download from a TIDAL URL (artist, album, track, video, playlist)."""
    Download(verbose=ctx.obj.get('verbose', False), config=ctx.obj.get('config')).download_url(url, force=force)


@download.command('artist')
@click.pass_context
@click.argument('identifier')
@click.option('--force', is_flag=True, default=False, help='Force re-download even if file exists.')
def download_artist(ctx, identifier, force):
    """Download full discography for an artist (name or ID)."""
    dl = Download(verbose=ctx.obj.get('verbose', False), config=ctx.obj.get('config'))
    try:
        artist_id = int(identifier)
        dl.download_artist(artist_id=artist_id, force=force)
    except ValueError:
        dl.download_artist(artist_name=identifier, force=force)


@download.command('album')
@click.pass_context
@click.argument('album_id', type=int)
@click.option('--force', is_flag=True, default=False, help='Force re-download even if file exists.')
def download_album(ctx, album_id, force):
    """Download an album by ID."""
    Download(verbose=ctx.obj.get('verbose', False), config=ctx.obj.get('config')).download_album(album_id, force=force)


@download.command('track')
@click.pass_context
@click.argument('track_id', type=int)
@click.option('--force', is_flag=True, default=False, help='Force re-download even if file exists.')
def download_track(ctx, track_id, force):
    """Download a track by ID."""
    Download(verbose=ctx.obj.get('verbose', False), config=ctx.obj.get('config')).download_track(track_id, force=force)


@download.command('monitored')
@click.pass_context
@click.option('--force', is_flag=True, default=False, help='Force re-download even if file exists.')
@click.option('--since', default=None, help='Only albums released since date (YYYY-MM-DD).')
@click.option('--until', default=None, help='Only albums released until date (YYYY-MM-DD).')
@click.option('--dry-run', is_flag=True, default=False, help='Show what would be downloaded without actually downloading.')
def download_monitored(ctx, force, since, until, dry_run):
    """Download pending albums for all monitored artists."""
    Download(verbose=ctx.obj.get('verbose', False), config=ctx.obj.get('config')).download_monitored(force=force, since=since, until=until, dry_run=dry_run)


@download.command('all')
@click.pass_context
@click.option('--force', is_flag=True, help='Force re-download of all albums, ignoring existing files.')
@click.option('--dry-run', is_flag=True, default=False, help='Show what would be downloaded without actually downloading.')
@click.option('--resume', is_flag=True, default=False, help='Resume an interrupted download, skipping completed albums.')
@click.option('--since', default=None, help='Only process albums released on or after this date (YYYY-MM-DD).')
@click.option('--until', default=None, help='Only process albums released on or before this date (YYYY-MM-DD).')
def download_all(ctx, force, dry_run, resume, since, until):
    """Download all albums from the database."""
    Download(verbose=ctx.obj.get('verbose', False), config=ctx.obj.get('config')).download_all(force=force, dry_run=dry_run, resume=resume, since=since, until=until)


# ── search ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument('query')
@click.option('--type', '-t', 'search_type',
              type=click.Choice(['artists', 'albums', 'tracks'], case_sensitive=False),
              default='artists', show_default=True)
@click.option('--limit', '-l', default=10, show_default=True)
@click.pass_context
def search(ctx, query, search_type, limit):
    """Search TIDAL for artists, albums, or tracks."""
    with Search(config=ctx.obj.get('config'), session=ctx.obj.get('session')) as s:
        if search_type == 'artists':
            s.search_artists(query, limit=limit)
        elif search_type == 'albums':
            s.search_albums(query, limit=limit)
        elif search_type == 'tracks':
            s.search_tracks(query, limit=limit)


# ── show ──────────────────────────────────────────────────────────────────────

@cli.group()
def show():
    """Show monitored data (artists, releases, etc.)."""
    pass


@show.command('artists')
@click.option('--artists', 'target', flag_value='artists', default=True,
              help='Show monitored artists (default).')
@click.option('--playlists', 'target', flag_value='playlists',
              help='Show monitored playlists instead.')
@click.option('--all', 'target', flag_value='all',
              help='Show both artists and playlists.')
@click.option('--csv', 'export_csv', is_flag=True, help='Export artists as CSV.')
@click.option('--output', '-o', default=None, help='Output file path for CSV.')
@click.pass_context
def show_artists(ctx, target, export_csv, output):
    """Show monitored artists and/or playlists."""
    with Show() as s:
        s.show_artists(export_csv=export_csv, export_path=output, target=target)


@show.command('releases')
@click.option('--days', '-d', default=30, show_default=True, help='Number of days to look back.')
@click.option('--future', '-f', is_flag=True, help='Show upcoming releases instead.')
@click.option('--export', default=None, metavar='FILE', help='Export to file: .csv for spreadsheet, any other ext for tiddl commands.')
def show_releases(days, future, export):
    """Show recent or upcoming releases."""
    with Show() as s:
        s.show_releases(days=days, future=future, export=export)


@show.command('albums')
@click.option('--artist', '-a', default=None, help='Filter by artist name or ID.')
@click.option('--pending', is_flag=True, help='Only show not yet downloaded.')
@click.option('--since', default=None, metavar='DATE', help='Only albums released on or after DATE (YYYY-MM-DD).')
@click.option('--until', default=None, metavar='DATE', help='Only albums released on or before DATE (YYYY-MM-DD).')
@click.option('--export', default=None, metavar='FILE', help='Export to file: .csv for spreadsheet, any other ext for tiddl commands.')
def show_albums(artist, pending, since, until, export):
    """Show albums in the database."""
    with Show() as s:
        s.show_albums(artist=artist, pending=pending, since=since, until=until, export=export)


@show.command('discography')
@click.option('--format', '-f', 'fmt',
              type=click.Choice(['csv', 'txt', 'html'], case_sensitive=False),
              multiple=True,
              default=['csv', 'txt', 'html'],
              show_default=True,
              help='Output format(s). Can be specified multiple times.')
@click.option('--output', '-o', default='.', show_default=True,
              help='Directory where the files will be saved.')
def show_discography(fmt, output):
    """Export artist discographies organized into A-Z files.

    Generates one file per letter (A-Z and #) for each requested format,
    containing all artists and their albums sorted by release date.

    \b
    Examples:
      tidmon show discography
      tidmon show discography --format csv --format html -o ~/Music/catalog
    """
    with Show() as s:
        s.show_discography(output_dir=output, formats=list(fmt))


# ── config ────────────────────────────────────────────────────────────────────

@cli.group()
def config():
    """View and modify tidmon configuration."""
    pass


@config.command('show')
def config_show():
    """Show all configuration values."""
    ConfigCommand().get_all()


@config.command('get')
@click.argument('key')
def config_get(key):
    """Get the value of a configuration key."""
    ConfigCommand().get_key(key)


@config.command('set')
@click.argument('key')
@click.argument('value')
def config_set(key, value):
    """Set a configuration value."""
    ConfigCommand().set_key(key, value)


@config.command('path')
def config_path():
    """Show the config file path."""
    ConfigCommand().path()


# ── backup ────────────────────────────────────────────────────────────────────

@cli.group()
def backup():
    """Backup and restore tidmon data."""
    pass


@backup.command('create')
@click.option('--output', '-o', default=None, help='Output archive path.')
@click.pass_context
def backup_create(ctx, output):
    """Create a backup of the database and config."""
    with Backup(config=ctx.obj.get('config')) as b:
        b.create(output_path=output)


@backup.command('restore')
@click.argument('path')
@click.pass_context
def backup_restore(ctx, path):
    """Restore from a backup archive."""
    with Backup(config=ctx.obj.get('config')) as b:
        b.restore(path)


@backup.command('list')
@click.pass_context
def backup_list(ctx):
    """List available backups."""
    with Backup(config=ctx.obj.get('config')) as b:
        b.list_backups()


@backup.command('delete')
@click.argument('path', required=False)
@click.option('--keep', '-k', 'keep_last', default=None, type=int,
              help='Keep only the N most recent backups.')
@click.pass_context
def backup_delete(ctx, path, keep_last):
    """Delete a backup or trim old ones."""
    with Backup(config=ctx.obj.get('config')) as b:
        b.delete(backup_path=path, keep_last=keep_last)


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    cli(obj={})


if __name__ == '__main__':
    run()