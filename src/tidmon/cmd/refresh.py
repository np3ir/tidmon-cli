import logging
import random
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from tidmon.core.db import Database
from tidmon.core.config import Config
from tidmon.core.auth import get_session
from tidmon.core.auth import TidalSession
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


class Refresh:
    """Check monitored artists and playlists for new releases."""

    def __init__(self, config: Config = None, session: TidalSession = None, anonymous: bool = True):
        self.config = config or Config()
        self.db = Database()
        self.session = session or get_session()
        # Detection runs anonymously by default (x-tidal-token only) so feeding the
        # DB never touches/rotates the personal account. Downloads, which create
        # their own Download() session, still use the logged-in account.
        self.anonymous = anonymous
        self._api = None
        self.new_releases = []
        self.new_playlist_tracks = []
        self.new_videos = []

    @property
    def api(self):
        if self._api is None:
            self._api = (self.session.get_anonymous_api()
                         if self.anonymous else self.session.get_api())
        return self._api

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()

    def __enter__(self) -> "Refresh":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def refresh(
            self,
            artist: str = None,
            artist_id: int = None,
            refresh_artists: bool = True,
            refresh_playlists: bool = True,
            since: str = None,
            until: str = None,
            album_since: str = None,
            album_until: str = None,
            download: bool = False,
            videos_only: bool = False,
            check_videos: bool = False,
            register_videos: bool = False,
            video_since: str = None,
            video_until: str = None,
            artist_delay: float = 0.0,
            stale_hours: float = None,
            max_artists: int = None,
            restart: bool = False,
    ):
        """Refresh monitored content and detect new releases."""
        try:
            logger.info("Starting refresh...")

            if restart:
                n = self.db.reset_all_check_times()
                logger.info(f"--restart: cleared progress for {n} artist(s).")
                console.print(f"  [yellow]↺ Restart:[/] progreso reseteado ({n:,} artistas) — empezando desde el principio.")

            # Check if there's anything to do if no specific artist is given
            if not artist_id and not artist and not self.db.get_all_artists() and not self.db.get_monitored_playlists():
                console.print("\n  No artists or playlists are being monitored.")
                console.print("  Use 'tidmon monitor add <artist/playlist>' to get started.")
                return

            if refresh_artists:
                self._refresh_artists(artist_id, artist, since, until, album_since, album_until,
                                      artist_delay, stale_hours, max_artists)

            if refresh_playlists:
                self._refresh_all_playlists()

            if check_videos or register_videos or (download and videos_only):
                self._collect_new_videos(videos_only=True, since=video_since, until=video_until)
            elif download and self.new_releases:
                self._collect_new_videos(videos_only=False)

            if register_videos and self.new_videos:
                self._register_videos()

            self._show_summary()

            if download and (self.new_releases or self.new_videos):
                self._download_new_releases(videos_only=videos_only)

            if self.config.email_notifications_enabled() and (self.new_releases or self.new_playlist_tracks):
                self._send_email_notification()

        except ConnectionError as e:
            logger.error(f"Authentication failed: {e}")
            print(f"\n❌ Error: {e}")
            print("   Run 'tidmon auth' to log in.")

    def _refresh_artists(self, artist_id, artist, since, until, album_since, album_until,
                         artist_delay: float = 0.0, stale_hours: float = None, max_artists: int = None):
        if artist_id:
            artists = [self.db.get_artist(artist_id)]
            if not artists[0]:
                logger.error(f"Artist ID {artist_id} not found")
                return
        elif artist:
            artist_obj = self.db.get_artist_by_name(artist)
            if not artist_obj:
                logger.error(f"Artist '{artist}' not found")
                return
            artists = [artist_obj]
        else:
            checked_before = None
            if stale_hours is not None:
                checked_before = (datetime.now() - timedelta(hours=stale_hours)).isoformat()
            artists = self.db.get_all_artists(since=since, until=until, checked_before=checked_before)

        if not artists:
            logger.warning("No artists to refresh")
            return

        if max_artists is not None and len(artists) > max_artists:
            console.print(f"  [yellow]Tope de volumen:[/] procesando {max_artists} de {len(artists)} artistas este run.")
            artists = artists[:max_artists]

        console.print(f"\n{'=' * 60}")
        console.print(f"  REFRESHING {len(artists)} ARTIST(S)")
        console.print(f"{'=' * 60}\n")

        # Circuit-breaker: si muchos artistas seguidos fallan en la API, asumimos un
        # bloqueo sistemico (DataDome/bot, cuenta o red) y paramos en vez de seguir
        # martillando (lo que refuerza el bloqueo). El progreso ya quedo guardado en
        # last_checked, asi que un re-run continua donde quedo.
        consecutive_fail = 0
        FAIL_ABORT = 10
        PAUSE_EVERY = 250
        for i, artist_obj in enumerate(artists):
            ok = self._refresh_artist(artist_obj, album_since, album_until)
            if ok:
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                if consecutive_fail >= FAIL_ABORT:
                    console.print(f"\n  [red bold]ABORTANDO:[/] {consecutive_fail} artistas seguidos fallaron en la API.")
                    console.print("  Probable bloqueo (DataDome/bot, cuenta o red). El progreso quedo guardado.")
                    console.print("  Para y revisa antes de reintentar — insistir refuerza el bloqueo de IP.")
                    logger.error(f"Refresh abortado tras {consecutive_fail} fallos de API seguidos (posible bloqueo bot/IP).")
                    return
            if artist_delay > 0 and i < len(artists) - 1:
                time.sleep(artist_delay)
            elif PAUSE_EVERY and (i + 1) % PAUSE_EVERY == 0 and i < len(artists) - 1:
                pause = random.uniform(20, 60)
                logger.info(f"Pausa larga {pause:.0f}s tras {i + 1} artistas (ritmo anti-bot).")
                time.sleep(pause)

    def _album_filters(self) -> list:
        """Map configured record_types → the TIDAL catalogue filters we must query.

        Each filter is a separate paginated request. If the user doesn't monitor a
        category (e.g. COMPILATION), skipping its filter saves one API round-trip per
        artist with no change in results (those albums were discarded anyway). Falls
        back to all filters if record_types is empty/unrecognised.
        """
        types = {t.upper() for t in self.config.record_types()}
        filters = []
        if "ALBUM" in types:
            filters.append("ALBUMS")
        if "EP" in types or "SINGLE" in types:
            filters.append("EPSANDSINGLES")
        if "COMPILATION" in types:
            filters.append("COMPILATIONS")
        return filters or ["ALBUMS", "EPSANDSINGLES", "COMPILATIONS"]

    def _refresh_artist(self, artist: dict, album_since: str = None, album_until: str = None):
        artist_id = artist['artist_id']
        artist_name = artist['artist_name']

        logger.info(f"Refreshing: {artist_name}")
        console.print(f"  [dim]-[/] {artist_name}...", end='')

        # Parse --album-since up-front so we can hand it to the API as an
        # early-termination hint: the catalogue endpoint returns albums
        # newest-first, so it can stop paginating once it walks past this date
        # instead of pulling an artist's entire back-catalogue every refresh.
        since_date = None
        if album_since:
            try:
                since_date = datetime.strptime(album_since, "%Y-%m-%d").date()
            except ValueError:
                logger.error("Invalid --album-since date format. Use YYYY-MM-DD.")
                print("  (invalid date format)")
                return False

        api_albums = self.api.get_artist_albums(
            artist_id, filters=self._album_filters(), released_since=since_date
        )

        if api_albums is None:
            console.print(f"  [red]x[/] API error (skipped, will retry next refresh)")
            logger.warning(f"API returned no data for {artist_name} (ID: {artist_id}) — not updating check time")
            return False

        # Date-filter the collected albums. This stays as the correctness
        # guarantee — the API early-termination is only an optimisation and may
        # return a few extra (older) items from the final page it fetched.
        if since_date:
            api_albums = [a for a in api_albums if a.release_date and a.release_date.date() >= since_date]

        if album_until:
            try:
                until_date = datetime.strptime(album_until, "%Y-%m-%d").date()
                api_albums = [a for a in api_albums if a.release_date and a.release_date.date() <= until_date]
            except ValueError:
                logger.error("Invalid --album-until date format. Use YYYY-MM-DD.")
                print("  (invalid date format)")
                return False

        db_albums = self.db.get_artist_albums(artist_id)
        db_album_ids = {album['album_id'] for album in db_albums}

        new_count = 0
        allowed_types = self.config.record_types()

        for album in api_albums:
            if album.type and album.type.upper() not in [t.upper() for t in allowed_types]:
                continue
            # Skip Various Artists compilations
            album_artist = getattr(album.artist, 'name', '') if album.artist else ''
            if album_artist.lower() in ('various artists', 'varios artistas', 'varios'):
                logger.debug(f"Skipping Various Artists album: {album.title} ({album.id})")
                continue
            if album.id not in db_album_ids:
                if self.db.add_album(album, artist_id):
                    new_count += 1
                    self.new_releases.append({
                        'artist_name': artist_name,
                        'album': album
                    })
                    logger.info(f"  → New release: {album.title}")

        if new_count > 0:
            console.print(f"  [green]+[/] {new_count} new release(s)")
        else:
            console.print(f"  [dim]ok[/] up to date")

        self.db.update_artist_check_time(artist_id)
        return True

    def _refresh_all_playlists(self):
        playlists = self.db.get_monitored_playlists()
        if not playlists:
            return

        console.print(f"\n{'=' * 60}")
        console.print(f"  REFRESHING {len(playlists)} PLAYLIST(S)")
        console.print(f"{'=' * 60}\n")

        for playlist in playlists:
            self._refresh_playlist(playlist)

    def _refresh_playlist(self, playlist: dict):
        playlist_uuid = playlist['uuid']
        playlist_name = playlist['name']

        logger.info(f"Refreshing playlist: {playlist_name}")
        console.print(f"  [dim]-[/] {playlist_name}...", end='')

        try:
            current_tracks = self.api.get_playlist_items(playlist_uuid)
            current_track_ids = {t.id for t in current_tracks if hasattr(t, 'id')}
            known_track_ids = self.db.get_playlist_track_ids(playlist_uuid)
            new_track_ids = current_track_ids - known_track_ids

            if not new_track_ids:
                console.print(f"  [dim]ok[/] no new tracks")
            else:
                console.print(f"  [green]+[/] {len(new_track_ids)} new track(s)")
                new_tracks = [t for t in current_tracks if t.id in new_track_ids]
                self.new_playlist_tracks.append({
                    'playlist_name': playlist_name,
                    'tracks': new_tracks
                })
                self.db.update_playlist_tracks(playlist_uuid, current_track_ids)

            self.db.update_playlist_check_time(playlist_uuid)

        except Exception as e:
            logger.error(f"Failed to refresh playlist '{playlist_name}': {e}")
            console.print(f"  [red]x[/] error: {e}")

    def _show_summary(self):
        console.print(f"\n{'=' * 60}")
        console.print("  REFRESH SUMMARY")
        console.print(f"{'=' * 60}\n")

        if not self.new_releases and not self.new_playlist_tracks and not self.new_videos:
            console.print("  No new releases, videos, or playlist changes detected.\n")
            return

        if self.new_releases:
            console.print(f"  NEW RELEASES ({len(self.new_releases)}):\n")
            for release in self.new_releases:
                album = release['album']
                release_date = album.release_date.strftime('%Y-%m-%d') if album.release_date else "?"
                console.print(f"    [bold]{release['artist_name']}[/] - {album.title}")
                console.print(f"      Type: {album.type or 'ALBUM'}  |  Date: {release_date}  |  ID: {album.id}")
            console.print()

        if self.new_videos:
            console.print(f"  NEW VIDEOS ({len(self.new_videos)}):\n")
            for item in self.new_videos:
                video = item['video']
                release_date = video.release_date.strftime('%Y-%m-%d') if video.release_date else "?"
                console.print(f"    [bold]{item['artist_name']}[/] - {video.title}")
                console.print(f"      Date: {release_date}  |  ID: {video.id}")
            console.print()

        if self.new_playlist_tracks:
            console.print(f"  NEW PLAYLIST TRACKS:\n")
            for item in self.new_playlist_tracks:
                console.print(f"    Playlist: {item['playlist_name']}")
                for track in item['tracks']:
                    artists = ", ".join([a.name for a in track.artists]) if track.artists else "Unknown"
                    console.print(f"      - {track.title} by {artists}")
            console.print()

        console.print(f"{'=' * 60}\n")

    def _collect_new_videos(self, videos_only: bool = False, since: str = None, until: str = None):
        """Fetch and detect new videos.

        videos_only=False: check only artists that have new album releases.
        videos_only=True:  check all monitored artists, optionally filtered by
                           added_date (since/until).
        """
        if not self.config.save_video_enabled():
            return

        if videos_only:
            artists = self.db.get_all_artists(since=since, until=until)
            targets = [(a['artist_id'], a['artist_name']) for a in artists]
        else:
            seen = {}
            for r in self.new_releases:
                aid = r['album'].artist.id if r['album'].artist else None
                if aid and aid not in seen:
                    seen[aid] = r['artist_name']
            targets = list(seen.items())

        if not targets:
            return

        console.print(f"\n{'=' * 60}")
        console.print(f"  CHECKING VIDEOS FOR {len(targets)} ARTIST(S)")
        console.print(f"{'=' * 60}\n")

        for artist_id, artist_name in targets:
            console.print(f"  [dim]-[/] {artist_name}...", end='')
            api_videos = self.api.get_artist_videos(artist_id)
            new_count = 0
            for video in (api_videos or []):
                if not self.db.is_video_downloaded(video.id):
                    self.new_videos.append({'artist_name': artist_name, 'video': video})
                    new_count += 1
            if new_count:
                console.print(f"  [green]+[/] {new_count} new video(s)")
            else:
                console.print(f"  [dim]ok[/] up to date")

    def _register_videos(self):
        """Add all detected videos to the DB as downloaded=1 without downloading files."""
        count = 0
        for item in self.new_videos:
            video = item['video']
            artist_name = video.artist.name if video.artist else item['artist_name']
            release_date = video.release_date.strftime('%Y-%m-%d') if video.release_date else None
            if self.db.mark_video_as_downloaded(video.id, video.title, artist_name, release_date):
                count += 1
        self.new_videos.clear()
        console.print(f"\n  [green]✓[/] {count} video(s) registered in DB (skipped on future runs).")

    def _download_new_releases(self, videos_only: bool = False):
        """Auto-download all newly detected releases and/or videos."""
        from tidmon.cmd.download import Download
        dl = Download()
        if not videos_only and self.new_releases:
            console.print("\n  AUTO-DOWNLOADING new releases...\n")
            for release in self.new_releases:
                album = release['album']
                console.print(f"  >> {release['artist_name']} - {album.title}")
                dl.download_album(album.id)
        if self.new_videos:
            console.print("\n  AUTO-DOWNLOADING new videos...\n")
            for item in self.new_videos:
                video = item['video']
                console.print(f"  >> {item['artist_name']} - {video.title}")
                dl.download_video(video.id)

    def _send_email_notification(self):
        """Send email notification about new releases."""
        smtp_server = self.config.get('smtp_server', 'smtp.gmail.com')
        smtp_port = self.config.get('smtp_port', 587)
        use_tls = self.config.get('smtp_use_tls', True)
        email_from = self.config.get('email_from', '')
        email_to = self.config.get('email_to', '')
        email_password = self.config.get('email_password', '')

        if not all([smtp_server, email_from, email_to, email_password]):
            logger.warning("Email notifications enabled but SMTP settings incomplete.")
            console.print("  [yellow]Email configured but SMTP settings incomplete. Check config.[/]")
            return

        # Build message body
        lines = [
            f"tidmon refresh report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        if self.new_releases:
            lines.append(f"NEW RELEASES ({len(self.new_releases)}):")
            lines.append("")
            for r in self.new_releases:
                album = r['album']
                release_date = album.release_date.strftime('%Y-%m-%d') if album.release_date else "?"
                lines.append(f"  • {r['artist_name']} - {album.title}")
                lines.append(f"    {album.type or 'ALBUM'} | {release_date}")
                lines.append(f"    https://tidal.com/album/{album.id}")
                lines.append("")

        if self.new_playlist_tracks:
            lines.append("NEW PLAYLIST TRACKS:")
            lines.append("")
            for item in self.new_playlist_tracks:
                lines.append(f"  Playlist: {item['playlist_name']}")
                for track in item['tracks']:
                    artists = ", ".join([a.name for a in track.artists]) if track.artists else "Unknown"
                    lines.append(f"    → {track.title} by {artists}")
                lines.append("")

        body = "\n".join(lines)

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"tidmon: {len(self.new_releases)} new release(s) detected"
        msg['From'] = email_from
        msg['To'] = email_to
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                if use_tls:
                    server.starttls()
                server.login(email_from, email_password)
                server.sendmail(email_from, email_to, msg.as_string())
            logger.info(f"Email notification sent to {email_to}")
            console.print(f"  [green]Email notification sent to {email_to}[/]")
        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP authentication failed. Check email/password in config.")
            console.print("  [red]Email failed: authentication error. Check smtp settings.[/]")
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
            console.print(f"  [red]Email failed: {e}[/]")