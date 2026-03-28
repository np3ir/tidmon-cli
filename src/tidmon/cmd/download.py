import logging
import asyncio
import sys
from pathlib import Path
from typing import Optional, List
from collections import Counter
from tidmon.core.auth import TidalSession

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
    SpinnerColumn,
    FileSizeColumn,
    MofNCompleteColumn,
    ProgressColumn,
    Task as RichTask,
    TaskID,
)

from tidmon.core.db import Database
from tidmon.core.config import Config
from tidmon.core.downloader import AdvancedDownloader, DownloadTask, DownloadPriority, DownloadStatus
from tidmon.core.models.resources import Track, Album, Artist, Video
from tidmon.core.utils.parse import parse_track_stream, parse_video_stream
from tidmon.core.utils.ffmpeg import extract_flac, fix_mp4_faststart, convert_to_mp4
from tidmon.core.utils.format import format_template, DEFAULT_ARTIST_SEPARATOR
from tidmon.core.utils.cover import Cover
from tidmon.core.utils.metadata import add_track_metadata, add_video_metadata
from tidmon.core.utils.deezer import get_genre_from_deezer
from tidmon.core.utils.url import parse_url, TidalType
from tidmon.core.auth import get_session

logger = logging.getLogger(__name__)

# Template defaults live in Config.DEFAULT_CONFIG["templates"] so they are
# always visible in config.json and never duplicated here.

# Quality color labels — match tiddl's style
QUALITY_COLORS = {
    "MAX":              "[bold yellow]MAX",
    "HI_RES_LOSSLESS":  "[yellow]Hi-Res Lossless",
    "LOSSLESS":         "[cyan]Lossless",
    "HIGH":             "[white]320 kbps",
    "LOW":              "[dim]96 kbps",
}


class _TimeElapsedColumn(ProgressColumn):
    """Renders time elapsed for the total progress bar."""
    def render(self, task: RichTask) -> Text:
        elapsed = task.finished_time if task.finished else task.elapsed
        if elapsed is None:
            return Text("---", style="progress.elapsed")
        return Text(f"{elapsed:.1f}s", style="progress.elapsed")


class RichUI:
    """
    Two-panel Rich UI that mirrors tiddl's visual style:
      ┌ Downloading ──────────────────────────────────────────────┐
      │ ⠿  Artist - Track Title  [Lossless]  1.2 MB  3.4 MB/s   │
      └───────────────────────────────────────────────────────────┘
      ┌ Total Progress ───────────────────────────────────────────┐
      │  12.3s  ████████████████░░░░  8 / 12                     │
      └───────────────────────────────────────────────────────────┘
    """

    def __init__(self) -> None:
        self.console = Console()

        self.dl_progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            FileSizeColumn(),
            TransferSpeedColumn(),
            console=self.console,
        )
        self.total_progress = Progress(
            _TimeElapsedColumn(),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            console=self.console,
        )
        self._group = Group(
            Panel(
                self.dl_progress,
                title="Downloading",
                border_style="magenta",
                title_align="left",
            ),
            Panel(
                self.total_progress,
                title="Total Progress",
                border_style="green",
                title_align="left",
            ),
        )
        self._live = Live(self._group, console=self.console, refresh_per_second=12)
        self._total_task: TaskID | None = None
        self._total = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self, total: int) -> None:
        """Start the Live display for a new batch, resetting previous state."""
        # Clear all leftover tasks from the previous album
        for task_id in list(self.dl_progress.task_ids):
            self.dl_progress.remove_task(task_id)
        for task_id in list(self.total_progress.task_ids):
            self.total_progress.remove_task(task_id)

        self._total = total
        self._total_task = self.total_progress.add_task("", total=total)
        self._live.start()

    def stop(self) -> None:
        """Stop the Live display."""
        self._live.stop()

    # ── per-track API ────────────────────────────────────────────────────────

    def track_start(self, description: str, total_bytes: int | None = None) -> TaskID:
        return self.dl_progress.add_task(description, total=total_bytes)

    def track_advance(self, task_id: TaskID, chunk: int) -> None:
        self.dl_progress.update(task_id, advance=chunk)

    def track_finish(self, task_id: TaskID) -> None:
        self.dl_progress.remove_task(task_id)
        if self._total_task is not None:
            self.total_progress.advance(self._total_task, 1)

    def track_finish_silent(self) -> None:
        """Advance total counter without removing a dl_progress bar (for pre-skipped tasks)."""
        if self._total_task is not None:
            self.total_progress.advance(self._total_task, 1)

    # ── result lines printed below the panels ────────────────────────────────

    def print_result(self, status: str, description: str, path=None) -> None:
        if path:
            msg = (
                f"{status} "
                f"[link={path.as_uri()}]{description}[/link] "
                f"[link={path.parent.as_uri()}]{path.parent}[/link]"
            )
        else:
            msg = f"{status} {description}"
        self.console.print(msg)

    def print(self, msg: str) -> None:
        self.console.print(msg)


class Download:

    """Integrated download manager for tidemon"""

    def __init__(self, verbose: bool = False, config: Config = None, session: TidalSession = None):
        self.config = config or Config()
        self.db = Database()
        self.session = session or get_session()
        self._api = None
        self.verbose = verbose
        
        download_dir = self.config.download_path()
        if download_dir:
            Path(download_dir).mkdir(parents=True, exist_ok=True)
        
        video_download_dir = self.config.download_path(media_type='video')
        if video_download_dir:
            Path(video_download_dir).mkdir(parents=True, exist_ok=True)

        self.ui = RichUI()
        self.downloader = AdvancedDownloader(max_concurrent=3, on_progress=self._progress_callback)
        self.current_tasks: dict = {}

    @property
    def api(self):
        if self._api is None:
            self._api = self.session.get_api()
        return self._api

    def _progress_callback(self, task: DownloadTask):
        key = task.track_id

        # ── Active download bar ──────────────────────────────────────────────
        if task.status == DownloadStatus.DOWNLOADING:
            if key not in self.current_tasks:
                quality_label = QUALITY_COLORS.get(getattr(task, 'quality', ''), '')
                description = task.track_title or 'Unknown'
                if quality_label:
                    description = f"{description}  {quality_label}"
                self.current_tasks[key] = self.ui.track_start(
                    description, total_bytes=task.expected_size
                )
            else:
                self.ui.dl_progress.update(
                    self.current_tasks[key], completed=task.bytes_downloaded
                )
            return

        # ── Terminal states: print result line + remove bar ──────────────────
        if task.status in [DownloadStatus.COMPLETED, DownloadStatus.SKIPPED, DownloadStatus.FAILED]:
            if key in self.current_tasks:
                self.ui.track_finish(self.current_tasks[key])
                del self.current_tasks[key]

            path = task.output_path if task.output_path and task.output_path.exists() else None
            title = task.track_title or 'Unknown'

            if task.status == DownloadStatus.COMPLETED:
                self.ui.print_result("[green]Downloaded", title, path)
            elif task.status == DownloadStatus.SKIPPED:
                self.ui.print_result("[yellow]Exists", title, path)
            elif task.status == DownloadStatus.FAILED:
                err = f" — {task.error_message}" if task.error_message else ""
                self.ui.print_result(f"[red]Failed{err}", title, None)

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()

    def __enter__(self) -> "Download":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _cleanup_partial_files(self) -> None:
        """Deletes .part files left over from interrupted downloads."""
        download_root = self.config.download_path() or "Downloads"
        video_root    = self.config.download_path(media_type='video') or "Downloads/Videos"
        removed = 0
        for root in [download_root, video_root]:
            p = Path(root)
            if p.exists():
                for f in p.rglob("*.part.*"):
                    try:
                        f.unlink()
                        removed += 1
                        logger.debug(f"Removed partial file: {f}")
                    except Exception as e:
                        logger.warning(f"Could not remove {f}: {e}")
        if removed:
            self.ui.console.print(f"[dim]🧹 {removed} partial file(s) cleaned up.[/]")

    def _run_async(self, coro):
        """Wrapper to run async functions, handle auth errors, and clean up resources."""

        async def managed_run():
            try:
                await coro
            finally:
                await self.downloader.close()

        try:
            if sys.platform == 'win32':
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(managed_run())
        except KeyboardInterrupt:
            # Ctrl+C — stop Live display and clean up partial files
            try:
                self.ui.stop()
            except Exception:
                pass
            self.ui.console.print("\n[yellow]⚠️  Download interrupted by user.[/]")
            self._cleanup_partial_files()
        except ConnectionError as e:
            logger.error(f"Authentication failed: {e}")
            self.ui.print(f"[red]\n❌ Authentication error:[/] {e}")
            self.ui.print("   Run [bold]tidmon auth[/] to log in.")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            self.ui.print(f"[red]\n❌ Unexpected error:[/] {e}")
            self.ui.print("   Check [dim]~/.tidmon/logs/tidmon.log[/] for more details.")

    def _print_summary(self, title: str, global_stats: Counter):
        c, s, f = global_stats['completed'], global_stats['skipped'], global_stats['failed']
        self.ui.console.rule(f"[bold]{title.upper()} SUMMARY")
        self.ui.console.print(f"  [green]✅ Completed:[/]  {c}")
        self.ui.console.print(f"  [yellow]⚠️  Skipped:[/]    {s}")
        self.ui.console.print(f"  [red]❌ Failed:[/]     {f}")
        self.ui.console.rule()

    async def _apply_post_processing(
        self,
        tasks: list[DownloadTask],
        tracks: list[Track],
        album: Album,
    ) -> None:
        """
        Download album cover and apply metadata to all successfully downloaded tracks.
        Extracted to eliminate duplication between _download_album_async and
        _download_track_async.
        """
        # Cover
        cover_data = None
        try:
            if tasks and album.cover and (
                self.config.save_cover_enabled() or self.config.embed_cover_enabled()
            ):
                album_dir = tasks[0].output_path.parent
                logger.debug("Downloading cover...")
                cover = Cover(album.cover)
                cover_data = await asyncio.to_thread(cover._get_data)
                cover.data = cover_data
                if self.config.save_cover_enabled():
                    await asyncio.to_thread(cover.save_to_directory, album_dir / "cover")
        except Exception as e:
            logger.error(f"Error downloading cover: {e}")

        # Metadata
        logger.debug("Applying metadata...")
        task_map = {t.track_id: t for t in tasks}
        for track in tracks:
            task = task_map.get(track.id)
            if not (task and task.output_path.exists()):
                continue
            try:
                lyrics_text = None
                lrc_path = task.output_path.with_suffix(".lrc")
                if lrc_path.exists():
                    lyrics_text = lrc_path.read_text(encoding="utf-8")
                else:
                    txt_path = task.output_path.with_suffix(".txt")
                    if txt_path.exists():
                        lyrics_text = txt_path.read_text(encoding="utf-8")
                # Fix 6 — pass genre from album/track, fallback to Deezer if missing
                genre = (
                    getattr(track.album, "genre", None)
                    or getattr(album, "genre", None)
                )
                if not genre and track.isrc:
                    genre = get_genre_from_deezer(track.isrc)
                add_track_metadata(
                    path=task.output_path,
                    track=track,
                    album=album,
                    lyrics=lyrics_text,
                    cover_data=cover_data if self.config.embed_cover_enabled() else None,
                    genre=genre,
                    artist_separator=self.config.get("artist_separator", DEFAULT_ARTIST_SEPARATOR),
                )
            except Exception as e:
                logger.error(f"Error applying metadata to {task.track_title}: {e}")


    # --- Public Synchronous Methods (Entry Points) ---

    def download_album(self, album_id: int, force: bool = False):
        self._run_async(self._download_album_async(album_id, force=force))

    def download_artist(self, artist_name: str = None, artist_id: int = None, force: bool = False):
        self._run_async(self._download_artist_async(artist_name, artist_id, force=force))

    def download_track(self, track_id: int, force: bool = False):
        self._run_async(self._download_track_async(track_id, force=force))

    def download_video(self, video_id: int, force: bool = False):
        self._run_async(self._download_video_async(video_id, force=force))

    def download_url(self, url: str, force: bool = False):
        self._run_async(self._download_url_async(url, force=force))

    def download_monitored(self, force: bool = False, since: str = None, until: str = None, dry_run: bool = False):
        self._run_async(self._download_monitored_async(force=force, since=since, until=until, dry_run=dry_run))

    def download_all(self, force: bool = False, dry_run: bool = False, resume: bool = False, since: str = None, until: str = None):
        self._run_async(self._download_all_async(force=force, dry_run=dry_run, resume=resume, since=since, until=until))

    # --- Private Asynchronous Methods (Core Logic) ---

    async def _download_album_async(self, album_id: int, force: bool = False, show_summary: bool = True) -> Counter:
        album = self.api.get_album(album_id)
        if not album:
            self.ui.print(f"[red]❌ Album {album_id} not found")
            return Counter()

        artist_name = album.artist.name if album.artist else 'Unknown Artist'
        album_title = album.title or 'Unknown Album'
        self.ui.console.rule(f"[bold magenta]Album {album_id}")
        self.ui.print(f"[bold]📀 {album_title}[/] [dim]- {artist_name}")
        
        tracks = self.api.get_album_tracks(album_id)
        if not tracks:
            self.ui.print("[yellow]⚠️ No tracks found in album")
            return Counter()
        
        self.ui.print(f"   [dim]{len(tracks)} tracks")
        self.ui.print("\n   [dim]Preparing downloads...[/]")

        # Prepare all tracks in parallel, capped at 4 concurrent API calls
        # to avoid rate-limiting on the stream/lyrics endpoints.
        _prep_sem = asyncio.Semaphore(4)

        async def _prepare(i: int, track):
            track.track_number = track.track_number or i
            track.volume_number = track.volume_number or 1
            async with _prep_sem:
                return await self._process_track(track, album, force=force)

        results = await asyncio.gather(
            *[_prepare(i, t) for i, t in enumerate(tracks, 1)],
            return_exceptions=False,
        )
        tasks = [t for t in results if t]

        if not tasks:
            self.ui.print("[red]❌ Could not prepare downloads")
            return Counter()

        skipped_tasks = [t for t in tasks if t.status == DownloadStatus.SKIPPED]
        active_tasks  = [t for t in tasks if t.status != DownloadStatus.SKIPPED]

        # Print skipped result lines immediately
        for t in skipped_tasks:
            path = t.output_path if t.output_path and t.output_path.exists() else None
            self.ui.print_result("[yellow]Exists", t.track_title or 'Unknown', path)

        # Download cover once before the track loop so it can be embedded
        # in each track as soon as it finishes — same order as tiddl.
        cover_data = None
        try:
            if album.cover and (
                self.config.save_cover_enabled() or self.config.embed_cover_enabled()
            ):
                album_dir = tasks[0].output_path.parent
                cover = Cover(album.cover)
                cover_data = await asyncio.to_thread(cover._get_data)
                cover.data = cover_data
                if self.config.save_cover_enabled():
                    await asyncio.to_thread(cover.save_to_directory, album_dir / "cover")
        except Exception as e:
            logger.error(f"Error downloading cover: {e}")

        self.downloader.reset_stats()
        self.current_tasks.clear()

        # Concurrent download: up to concurrent_downloads tracks in parallel
        track_map = {t.id: t for t in tracks}
        session   = await self.downloader._get_session()
        _track_sem = asyncio.Semaphore(self.config.concurrent_downloads())

        async def _download_one(task):
            async with _track_sem:
                # 1. Download audio file
                await self.downloader.download_file(task, session)

                # 2. Apply metadata immediately after audio is on disk
                if task.output_path and task.output_path.exists():
                    track = track_map.get(task.track_id)
                    if track:
                        try:
                            lyrics_text = None
                            lrc_path = task.output_path.with_suffix(".lrc")
                            if lrc_path.exists():
                                lyrics_text = lrc_path.read_text(encoding="utf-8")
                            else:
                                txt_path = task.output_path.with_suffix(".txt")
                                if txt_path.exists():
                                    lyrics_text = txt_path.read_text(encoding="utf-8")
                            genre = (
                                getattr(track.album, "genre", None)
                                or getattr(album, "genre", None)
                            )
                            if not genre and track.isrc:
                                genre = get_genre_from_deezer(track.isrc)
                            add_track_metadata(
                                path=task.output_path,
                                track=track,
                                album=album,
                                lyrics=lyrics_text,
                                cover_data=cover_data if self.config.embed_cover_enabled() else None,
                                genre=genre,
                                artist_separator=self.config.get("artist_separator", DEFAULT_ARTIST_SEPARATOR),
                            )
                        except Exception as e:
                            logger.error(f"Error applying metadata to {task.track_title}: {e}")

        self.ui.start(total=len(tasks))
        for _ in skipped_tasks:
            self.ui.track_finish_silent()
        try:
            await asyncio.gather(*[_download_one(task) for task in active_tasks])
        finally:
            self.ui.stop()

        stats = dict(self.downloader.get_stats())
        stats["skipped"] = stats.get("skipped", 0) + len(skipped_tasks)

        if stats['failed'] == 0:
            try:
                self.db.mark_album_as_downloaded(album_id)
                logger.info(f"Album {album_id} marked as downloaded.")
            except Exception as e:
                logger.error(f"Failed to mark album {album_id} as downloaded: {e}")
        
        if show_summary:
            self._print_summary(f"Album: {album_title}", Counter(stats))
            for t in tasks:
                if t.status == DownloadStatus.FAILED and t.error_message:
                    self.ui.print(f"      [red]→ {t.track_title}:[/] {t.error_message}")
        return Counter(stats)

    async def _download_artist_async(self, artist_name: str = None, artist_id: int = None, force: bool = False):
        if artist_name and not artist_id:
            results = self.api.search(artist_name, 'ARTISTS', limit=1)
            artist = results.artists.items[0] if results and results.artists and results.artists.items else None
            if not artist:
                self.ui.print(f"[red]❌ Artist '{artist_name}' not found")
                return
            artist_id = artist.id
            artist_name = artist.name

        if not artist_id:
            self.ui.print("[red]❌ Artist ID or name required")
            return

        global_stats = Counter()
        self.ui.print(f"\n[bold]🔍 Searching content for {artist_name}[/] [dim]({artist_id})")
        
        albums = self.api.get_artist_albums(artist_id)
        if not albums:
            self.ui.print("[yellow]⚠️ No albums found for artist.")
        else:
            self.ui.print(f"[bold]📀 Found {len(albums)} albums.[/] Starting download...\n")
            skipped_va = 0
            for i, album in enumerate(albums, 1):
                album_artist = getattr(album.artist, 'name', '') if album.artist else ''
                if album_artist.lower() in ('various artists', 'varios artistas', 'varios'):
                    skipped_va += 1
                    logger.debug(f"Skipping Various Artists album: {album.title} ({album.id})")
                    continue
                self.ui.print(f"[dim]-> ALBUM [{i}/{len(albums)}]")
                album_stats = await self._download_album_async(album.id, force=force)
                global_stats.update(album_stats)
            if skipped_va:
                self.ui.print(f"[dim]⏭ Skipped {skipped_va} Various Artists compilation(s)")
        
        if self.config.save_video_enabled():
            videos = self.api.get_artist_videos(artist_id)
            if not videos:
                self.ui.print("[yellow]\n⚠️ No videos found for artist.")
            else:
                self.ui.print(f"\n[bold]🎥 Found {len(videos)} videos.[/] Starting download...\n")
                for i, video in enumerate(videos, 1):
                    self.ui.print(f"[dim]-> VIDEO [{i}/{len(videos)}]")
                    video_stats = await self._download_video_async(video.id, force=force)
                    global_stats.update(video_stats)
        
        self._print_summary(f"Overall Artist Download", global_stats)

    async def _download_track_async(self, track_id: int, force: bool = False):
        track = self.api.get_track(track_id)
        if not track:
            self.ui.print(f"[red]❌ Track {track_id} not found")
            return
        album_id = track.album.id if track.album else None
        if not album_id:
            self.ui.print("[red]❌ Could not determine album for track")
            return
        album = self.api.get_album(album_id)
        if not album:
            self.ui.print("[red]❌ Album info not found")
            return

        self.ui.console.rule(f"[bold magenta]{track.title}")
        task = await self._process_track(track, album, force=force)
        if not task: self.ui.print("[red]❌ Could not prepare download"); return

        self.downloader.reset_stats()
        self.current_tasks.clear()
        self.ui.start(total=1)
        try:
            stats = await self.downloader.download_batch([task])
        finally:
            self.ui.stop()

        await self._apply_post_processing([task], [track], album)
        self._print_summary("Track Download", Counter(stats))

    async def _download_video_async(self, video_id: int, force: bool = False) -> Counter:
        video = self.api.get_video(video_id)
        if not video:
            self.ui.print(f"[red]❌ Video {video_id} not found")
            return Counter()

        self.ui.console.rule(f"[bold magenta]{video.title}")

        file_path_no_ext = self._build_output_path(
            item=video, media_type='video', template_key='video'
        )

        if not force:
            for ext_check in ['.mp4', '.mkv']:
                possible_path = file_path_no_ext.with_name(file_path_no_ext.name + ext_check)
                if possible_path.exists():
                    self.ui.print(f"[yellow]⚠️ Video '{video.title}' already exists. Skipping.")
                    return Counter({'skipped': 1})

        stream_data = self.api.get_video_stream(video_id)
        if not stream_data: logger.error(f"No stream data for video {video_id}"); return Counter({'failed': 1})
        urls, ext = parse_video_stream(stream_data)
        if not urls: logger.error(f"Could not parse stream for {video.title}"); return Counter({'failed': 1})

        output_path = file_path_no_ext.with_name(file_path_no_ext.name + ext)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self.downloader.reset_stats()
        # Fix 1: HLS streams return multiple segment URLs — concatenate all of them.
        # Single-URL streams (progressive MP4) use the regular download path.
        if len(urls) > 1:
            await self.downloader.download_segments(
                urls=urls,
                output_path=output_path,
                track_id=video_id,
                track_title=video.title,
            )
            stats = Counter(self.downloader.get_stats())
        else:
            task = DownloadTask(
                url=urls[0], output_path=output_path,
                track_id=video_id, track_title=video.title,
                priority=DownloadPriority.NORMAL,
            )
            stats = Counter(await self.downloader.download_batch([task]))

        if output_path.exists():
            try:
                add_video_metadata(path=output_path, video=video)
                logger.debug("Metadata applied")
            except Exception as e:
                logger.error(f"Error applying metadata: {e}")

        # Fix 8: print summary on all call paths (was missing for direct download_video() calls)
        self._print_summary(f"Video: {video.title}", stats)
        return stats

    async def _download_url_async(self, url: str, force: bool = False):
        parsed = parse_url(url)
        if not parsed: logger.error(f"Invalid Tidal URL: {url}"); return

        type_, id_val = parsed.tidal_type, parsed.tidal_id
        logger.info(f"Processing {type_.value} ID: {id_val}")
        
        if type_ == TidalType.ARTIST:
            await self._download_artist_async(artist_id=int(id_val), force=force)
        elif type_ == TidalType.ALBUM:
            await self._download_album_async(album_id=int(id_val), force=force)
        elif type_ == TidalType.TRACK:
            await self._download_track_async(track_id=int(id_val), force=force)
        elif type_ == TidalType.VIDEO:
            stats = await self._download_video_async(video_id=int(id_val), force=force)
            self._print_summary("Video Download", stats)
        elif type_ == TidalType.PLAYLIST:
            await self._import_artists_from_playlist_async(id_val)

    async def _process_album_batch(
        self,
        albums: list,
        force: bool = False,
        resume: bool = False,
        dry_run: bool = False,
        summary_title: str = "Download",
    ) -> None:
        """
        Process a list of album dicts fetched from the database.

        Shared by _download_monitored_async and _download_all_async to avoid
        duplicating the loop, dry-run display, resume logic and summary print.

        Args:
            albums:        List of album dicts from db.get_albums().
            force:         Re-download already downloaded files.
            resume:        Skip albums already marked as downloaded in the DB.
            dry_run:       Print what would be downloaded without doing it.
            summary_title: Label shown in the final summary rule.
        """
        # Skip Various Artists compilations
        va_names = ('various artists', 'varios artistas', 'varios')
        albums = [
            a for a in albums
            if (a.get("album_artist_name") or "").lower() not in va_names
        ]

        # Apply resume filter (skip albums already downloaded unless force)
        to_download = [
            a for a in albums
            if not (resume and not force and a.get("downloaded") == 1)
        ]
        skipped_for_resume = len(albums) - len(to_download)

        if dry_run:
            self.ui.print(
                f"[dim]\n-- DRY RUN: {len(to_download)} album(s) would be downloaded"
                + (f" ({skipped_for_resume} skipped by --resume)" if skipped_for_resume else "")
                + " --[/]"
            )
            for i, album in enumerate(to_download, 1):
                self.ui.print(f"  [dim][{i}/{len(to_download)}][/] {album['artist_name']} — {album['title']}")
            self.ui.print("[dim]\nRun without --dry-run to start the download.[/]")
            return

        total = len(to_download)
        self.ui.print(f"[bold]🔄 Downloading {total} album(s)...[/]\n")

        global_stats = Counter()
        for i, album in enumerate(to_download, 1):
            self.ui.print(f"[dim]-> ALBUM [{i}/{total}]")
            album_stats = await self._download_album_async(album["album_id"], force=force)
            global_stats.update(album_stats)

        if skipped_for_resume > 0:
            self.ui.print(f"\n[dim]🔄 Skipped {skipped_for_resume} album(s) due to --resume.[/]")

        self._print_summary(summary_title, global_stats)

    async def _download_monitored_async(self, force: bool = False, since: str = None, until: str = None, dry_run: bool = False):
        """Fetch pending (not yet downloaded) albums and pass them to _process_album_batch."""
        albums = self.db.get_albums(include_downloaded=False, since=since, until=until)
        if not albums:
            self.ui.print("[green]✅ Nothing to download.")
            return
        await self._process_album_batch(albums, force=force, dry_run=dry_run, summary_title="Monitored Download")

    async def _download_all_async(self, force: bool = False, dry_run: bool = False, resume: bool = False, since: str = None, until: str = None):
        """Fetch all albums from the DB and pass them to _process_album_batch."""
        albums = self.db.get_albums(include_downloaded=True, since=since, until=until)
        if not albums:
            date_hint = ""
            if since or until:
                parts = []
                if since: parts.append(f"since {since}")
                if until: parts.append(f"until {until}")
                date_hint = f" matching date filter ({', '.join(parts)})"
            self.ui.print(f"[green]✅ No albums in the database{date_hint} to download.")
            return
        if force and resume:
            self.ui.print("[yellow]⚠️  --force and --resume specified together. --force takes precedence; --resume will be ignored.")
        await self._process_album_batch(albums, force=force, resume=resume, dry_run=dry_run, summary_title="Total Download")

    async def _import_artists_from_playlist_async(self, playlist_uuid: str):
        self.ui.print(f"\n[bold]🔍 Fetching playlist {playlist_uuid}...")
        items = self.api.get_playlist_items(playlist_uuid)
        if not items:
            self.ui.print("[yellow]⚠️ Playlist empty or not found")
            return
            
        self.ui.print(f"[bold]📄 Found {len(items)} tracks.[/] Extracting artists...")
        added_count, seen_artists = 0, set()
        for item in items:
            artist = item.artist
            if artist and artist.id and artist.name and artist.id not in seen_artists:
                seen_artists.add(artist.id)
                if not self.db.get_artist(artist.id):
                    self.db.add_artist(artist.id, artist.name)
                    self.ui.print(f"  [green]+ Added:[/] {artist.name}")
                    added_count += 1
        self.ui.print(f"\n[green]✅ Imported {added_count} new artists[/] (Total unique: {len(seen_artists)})")
        self.ui.print("💡 Run [bold]tidmon download monitored[/] to download their discographies.")

    def _build_output_path(
        self,
        item,
        album=None,
        media_type: str = 'default',
        template_key: str = 'default',
    ) -> Path:
        """
        Build the destination path (without extension) for any downloadable item.

        Centralizes the template lookup + format_template call that was
        duplicated in _process_track and _download_video_async.

        Args:
            item:         Track, Video, or similar model.
            album:        Album model (required for tracks, None for videos).
            media_type:   Key for config.download_path() ('default' or 'video').
            template_key: Key inside config['templates'] ('default' or 'video').

        Returns:
            Absolute Path without file extension.
        """
        templates    = self.config.get('templates', {})
        template     = templates.get(template_key) or Config.DEFAULT_CONFIG['templates'][template_key]
        download_root = Path(
            self.config.download_path(media_type=media_type)
            or ('Downloads/Videos' if media_type == 'video' else 'Downloads')
        )
        kwargs = dict(item=item, with_asterisk_ext=False, artist_separator=self.config.get("artist_separator", DEFAULT_ARTIST_SEPARATOR))
        if album is not None:
            kwargs['album'] = album
        rel = Path(format_template(template, **kwargs))
        return rel if rel.is_absolute() else download_root / rel

    async def _process_track(self, track_data: Track, album_data: Album, template: str = None, force: bool = False) -> Optional[DownloadTask]:
        try:
            track_id, title = track_data.id, track_data.title
            file_path_no_ext = self._build_output_path(
                item=track_data, album=album_data, media_type='default', template_key='default'
            )
            
            if not force:
                for ext_check in ['.flac', '.m4a', '.mp4']:
                    possible_path = file_path_no_ext.with_name(file_path_no_ext.name + ext_check)
                    if possible_path.exists():
                        logger.debug(f"File exists: {possible_path}")
                        track_num = getattr(track_data, 'track_number', None)
                        display_title = f"{track_num:02d}. {title}" if track_num else title
                        return DownloadTask(url="skipped", output_path=possible_path, track_id=track_id, track_title=display_title, status=DownloadStatus.SKIPPED)

            stream_data = None
            for quality in self.config.quality_order():
                stream_data = self.api.get_track_stream(track_id, quality=quality)
                if stream_data: logger.debug(f"Got stream for {title} at quality: {quality}"); break
            
            if not stream_data: logger.error(f"No stream data for track {track_id}"); return None
            urls, ext = parse_track_stream(stream_data)
            if not urls: logger.error(f"Could not parse stream for {title}"); return None
            
            output_path = file_path_no_ext.with_name(file_path_no_ext.name + ext)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if self.config.save_lrc_enabled():
                lrc_filename = output_path.with_suffix('.lrc')
                txt_filename = output_path.with_suffix('.txt')
                if not lrc_filename.exists() and not txt_filename.exists():
                    try:
                        lyrics_data = self.api.get_track_lyrics(track_id)
                        if lyrics_data:
                            if lyrics_data.subtitles:
                                with open(lrc_filename, 'w', encoding='utf-8') as f: f.write(lyrics_data.subtitles)
                                logger.info(f"Saved .lrc lyrics for {title}")
                            elif lyrics_data.lyrics:
                                with open(txt_filename, 'w', encoding='utf-8') as f: f.write(lyrics_data.lyrics)
                                logger.info(f"Saved .txt lyrics for {title}")
                    except Exception as e: logger.warning(f"Could not save lyrics for {title}: {e}")

            track_num = getattr(track_data, 'track_number', None)
            display_title = f"{track_num:02d}. {title}" if track_num else title
            return DownloadTask(url=urls[0], output_path=output_path, track_id=track_id, track_title=display_title)
        except Exception as e:
            logger.error(f"Error preparing track {track_data.title}: {e}", exc_info=True)
            return None
