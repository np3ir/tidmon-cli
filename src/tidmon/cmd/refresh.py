import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from tidmon.core.db import Database
from tidmon.core.config import Config
from tidmon.core.auth import get_session
from tidmon.core.auth import TidalSession
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


class Refresh:
    """Check monitored artists and playlists for new releases."""

    def __init__(self, config: Config = None, session: TidalSession = None):
        self.config = config or Config()
        self.db = Database()
        self.session = session or get_session()
        self._api = None
        self.new_releases = []
        self.new_playlist_tracks = []
        self.new_videos = []

    @property
    def api(self):
        if self._api is None:
            self._api = self.session.get_api()
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
    ):
        """Refresh monitored content and detect new releases."""
        try:
            logger.info("Starting refresh...")

            # Check if there's anything to do if no specific artist is given
            if not artist_id and not artist and not self.db.get_all_artists() and not self.db.get_monitored_playlists():
                console.print("\n  No artists or playlists are being monitored.")
                console.print("  Use 'tidmon monitor add <artist/playlist>' to get started.")
                return

            if refresh_artists:
                self._refresh_artists(artist_id, artist, since, until, album_since, album_until)

            if refresh_playlists:
                self._refresh_all_playlists()

            self._show_summary()

            if download and (self.new_releases or self.new_videos):
                self._download_new_releases(videos_only=videos_only)

            if self.config.email_notifications_enabled() and (self.new_releases or self.new_playlist_tracks):
                self._send_email_notification()

        except ConnectionError as e:
            logger.error(f"Authentication failed: {e}")
            print(f"\n❌ Error: {e}")
            print("   Run 'tidmon auth' to log in.")

    def _refresh_artists(self, artist_id, artist, since, until, album_since, album_until):
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
            artists = self.db.get_all_artists(since=since, until=until)

        if not artists:
            logger.warning("No artists to refresh")
            return

        console.print(f"\n{'=' * 60}")
        console.print(f"  REFRESHING {len(artists)} ARTIST(S)")
        console.print(f"{'=' * 60}\n")
        for artist_obj in artists:
            self._refresh_artist(artist_obj, album_since, album_until)

    def _refresh_artist(self, artist: dict, album_since: str = None, album_until: str = None):
        artist_id = artist['artist_id']
        artist_name = artist['artist_name']

        logger.info(f"Refreshing: {artist_name}")
        console.print(f"  [dim]-[/] {artist_name}...", end='')

        api_albums = self.api.get_artist_albums(artist_id)

        if api_albums is None:
            console.print(f"  [red]x[/] API error (skipped, will retry next refresh)")
            logger.warning(f"API returned no data for {artist_name} (ID: {artist_id}) — not updating check time")
            return

        # Filter albums by release date if options are provided
        if album_since:
            try:
                since_date = datetime.strptime(album_since, "%Y-%m-%d").date()
                api_albums = [a for a in api_albums if a.release_date and a.release_date.date() >= since_date]
            except ValueError:
                logger.error("Invalid --album-since date format. Use YYYY-MM-DD.")
                print("  (invalid date format)")
                return

        if album_until:
            try:
                until_date = datetime.strptime(album_until, "%Y-%m-%d").date()
                api_albums = [a for a in api_albums if a.release_date and a.release_date.date() <= until_date]
            except ValueError:
                logger.error("Invalid --album-until date format. Use YYYY-MM-DD.")
                print("  (invalid date format)")
                return

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

        if self.config.save_video_enabled():
            api_videos = self.api.get_artist_videos(artist_id)
            if api_videos:
                new_video_count = 0
                for video in api_videos:
                    if not self.db.is_video_downloaded(video.id):
                        self.new_videos.append({'artist_name': artist_name, 'video': video})
                        new_video_count += 1
                if new_video_count:
                    console.print(f"  [green]+[/] {new_video_count} new video(s)")

        self.db.update_artist_check_time(artist_id)

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