from pathlib import Path
from datetime import datetime
import unicodedata
import logging
from typing import Optional, List

try:
    from mutagen.flac import FLAC as MutagenFLAC, Picture
    from mutagen.mp4 import MP4 as MutagenMP4, MP4Cover
    from mutagen.easymp4 import EasyMP4 as MutagenEasyMP4
except ImportError:
    pass

from tidmon.core.models.resources import Track, Album, Video, Contributor
from tidmon.core.utils.format import (
    clean_track_title as _clean_track_title,
    build_artist_string,
    DEFAULT_ARTIST_SEPARATOR,
)
from tidmon.core.utils.ffmpeg import is_ffmpeg_installed, convert_to_mp4

logger = logging.getLogger(__name__)


# ============================================================
# Date helper — identical to tiddl behavior
# ============================================================

def _parse_year(date_str: Optional[str]) -> Optional[int]:
    """
    Parse a date string and return only the year integer.
    tiddl stores only the year in both FLAC DATE and M4A ©day.
    """
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).year
    except Exception:
        # Fallback: grab first 4 chars if they look like a year
        if len(date_str) >= 4 and date_str[:4].isdigit():
            return int(date_str[:4])
    return None


# ============================================================
# FLAC metadata writing
# ============================================================

def add_flac_metadata(
    track_path: Path,
    title: str,
    track_number: str,
    disc_number: str,
    album_title: str,
    album_artist: str,
    artists: str,
    date: Optional[str],
    copyright_str: Optional[str],
    isrc: Optional[str],
    comment: Optional[str],
    bpm: Optional[int],
    lyrics: Optional[str],
    credits: List[Contributor],
    cover_data: Optional[bytes],
    genre: Optional[str] = None,
) -> None:
    """Write FLAC metadata tags using Mutagen — identical behavior to tiddl."""
    try:
        mutagen = MutagenFLAC(track_path)
    except Exception as e:
        logger.error(f"Error opening FLAC file {track_path}: {e}")
        return

    # Embed cover art
    if cover_data:
        picture = Picture()
        picture.data = cover_data
        picture.mime = "image/jpeg"
        picture.type = 3  # front cover
        mutagen.add_picture(picture)

    # FIX 1+2: Store only the year in DATE (not full datetime string).
    # FIX 2:   Remove stale YEAR tag instead of writing a duplicate.
    year = _parse_year(date)
    if "YEAR" in mutagen:
        del mutagen["YEAR"]

    mutagen.update(
        {
            "TITLE":       title,
            "TRACKNUMBER": track_number,
            "DISCNUMBER":  disc_number,
            "ALBUM":       album_title,
            "ALBUMARTIST": album_artist,
            "ARTIST":      artists,
            "DATE":        str(year) if year else "",   # FIX 1: year only, e.g. "2023"
            "COPYRIGHT":   copyright_str or "",
            "ISRC":        isrc or "",
            "COMMENT":     comment or "",
            "GENRE":       genre or "",
        }
    )

    if bpm:
        mutagen["BPM"] = str(bpm)
    if lyrics:
        mutagen["LYRICS"] = lyrics

    # FIX 5: Normalize credit keys to safe ASCII Vorbis comment keys
    for entry in credits:
        try:
            raw_key    = entry.type.upper()
            normalized = unicodedata.normalize("NFKD", raw_key)
            safe_key   = normalized.encode("ascii", "ignore").decode("ascii")
            safe_key   = safe_key.replace("=", "").strip()
            if safe_key:
                mutagen[safe_key] = [c.name for c in entry.contributors]
        except Exception as e:
            logger.debug(f"Skipping invalid credit tag '{entry.type}': {e}")

    mutagen.save()


# ============================================================
# M4A / MP4 metadata writing
# ============================================================

def add_m4a_metadata(
    track_path: Path,
    title: str,
    track_number: str,
    disc_number: str,
    album_title: str,
    album_artist: str,
    artists: str,
    date: Optional[str],
    copyright_str: Optional[str],
    comment: Optional[str],
    bpm: Optional[int],
    lyrics: Optional[str],
    cover_data: Optional[bytes],
    genre: Optional[str] = None,
) -> None:
    """Write M4A (MP4) metadata tags using Mutagen — identical behavior to tiddl."""
    try:
        mp4 = MutagenMP4(track_path)
    except Exception as e:
        logger.error(f"Error opening MP4 file {track_path}: {e}")
        return

    # Clean stale title atom
    if "\xa9nam" in mp4:
        del mp4["\xa9nam"]

    # Cover art
    if cover_data:
        mp4["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]

    # Lyrics
    if lyrics:
        mp4["\xa9lyr"] = [lyrics]

    # Core tags
    mp4["\xa9nam"] = title
    mp4["\xa9alb"] = album_title
    mp4["aART"]    = album_artist
    mp4["\xa9ART"] = artists

    # FIX 3: Store only the year in ©day (iTunes/Apple Music/Windows expects "2023")
    year = _parse_year(date)
    if year:
        mp4["\xa9day"] = str(year)

    if copyright_str:
        mp4["cprt"] = copyright_str
    if comment:
        mp4["\xa9cmt"] = comment
    if genre:
        mp4["\xa9gen"] = genre

    # Track and disc numbers
    try:
        track_no = int(track_number)
    except (ValueError, TypeError):
        track_no = 0
    try:
        disc_no = int(disc_number)
    except (ValueError, TypeError):
        disc_no = 0

    mp4["trkn"] = [(track_no, 0)]
    mp4["disk"] = [(disc_no, 0)]

    # FIX 6: tmpo atom requires integer — cast via int(float(...)) like tiddl
    if bpm:
        try:
            mp4["tmpo"] = [int(float(bpm))]
        except (ValueError, TypeError):
            pass

    mp4.save()


# ============================================================
# Track entry point
# ============================================================

def add_track_metadata(
    path: Path,
    track: Track,
    album: Album,
    lyrics: Optional[str] = None,
    cover_data: Optional[bytes] = None,
    comment: Optional[str] = None,
    genre: Optional[str] = None,
    artist_separator: str = DEFAULT_ARTIST_SEPARATOR,
) -> None:
    """
    Write FLAC or M4A metadata to a track file.

    Date handling (tiddl-identical):
      - Source:  album.release_date  (date object or ISO string)
      - Storage: year-only string in both DATE (FLAC) and ©day (M4A)
    """
    # Build artists string via shared helper — keeps metadata consistent with filename template.
    artists_str      = build_artist_string(track, artist_separator)
    album_artist_str = album.artist.name if album.artist else "Unknown Artist"

    # Build title including version so it matches {item.title_version} used in the filename template
    clean_title = _clean_track_title(track)
    ver = (track.version or "").strip()
    title_with_version = f"{clean_title} ({ver})" if ver else clean_title

    # FIX 4: Pass release_date as ISO string; _parse_year extracts the year
    # when writing tags — same pipeline as tiddl (caller passes string, writer extracts year)
    release_date_str: Optional[str] = None
    if album.release_date:
        if isinstance(album.release_date, datetime):
            release_date_str = album.release_date.isoformat()
        else:
            release_date_str = str(album.release_date)

    # genre: caller may pass explicit value; fall back to album field
    resolved_genre = genre or getattr(album, 'genre', None)

    common = dict(
        title        = title_with_version,
        track_number = str(track.track_number),
        disc_number  = str(track.volume_number),
        album_title  = album.title,
        album_artist = album_artist_str,
        artists      = artists_str,
        date         = release_date_str,
        copyright_str= track.copyright,
        comment      = comment,
        bpm          = track.bpm,
        lyrics       = lyrics,
        cover_data   = cover_data,
        genre        = resolved_genre,
    )

    ext = path.suffix.lower()

    if ext == ".flac":
        add_flac_metadata(
            path,
            **common,
            isrc    = track.isrc,
            credits = getattr(track, "credits", []) or [],
        )
    elif ext in (".m4a", ".mp4"):
        add_m4a_metadata(path, **common)
    else:
        logger.warning(f"Unsupported file extension for metadata: {ext}")


# ============================================================
# Video entry point
# ============================================================

def add_video_metadata(path: Path, video: Video) -> None:
    """
    Write metadata to an MP4 video file.
    TS files are converted to MP4 first via ffmpeg.
    """
    suffix = path.suffix.lower()

    if suffix == ".ts":
        if not is_ffmpeg_installed():
            logger.warning(f"Skipping video metadata — ffmpeg not installed: {path}")
            return
        try:
            path = convert_to_mp4(path)
        except Exception as e:
            logger.error(f"TS → MP4 conversion failed: {path} → {e}")
            return
    elif suffix != ".mp4":
        logger.warning(f"Skipping video metadata — not an MP4 file: {path}")
        return

    try:
        mutagen = MutagenEasyMP4(path)
    except Exception as e:
        logger.error(f"Could not open MP4 for metadata: {path} → {e}")
        return

    artists_str = ";".join([a.name.strip() for a in video.artists]) if video.artists else ""

    meta: dict = {
        "title":  video.title,
        "artist": artists_str,
    }

    if video.artist:
        meta["albumartist"] = video.artist.name
    if video.album and video.album.title:
        meta["album"] = video.album.title

    # Prefer releaseDate over streamStartDate — same priority as tiddl
    raw_date = video.release_date or video.stream_start_date
    if raw_date:
        # Store year only for consistency with track metadata
        year = _parse_year(str(raw_date))
        if year:
            meta["date"] = str(year)

    if video.track_number:
        meta["tracknumber"] = str(video.track_number)
    if video.volume_number:
        meta["discnumber"] = str(video.volume_number)

    try:
        mutagen.update({k: v for k, v in meta.items() if v is not None})
        mutagen.save(path)
    except Exception as e:
        logger.error(f"Could not save MP4 metadata: {path} → {e}")