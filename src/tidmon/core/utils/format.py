import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union, Any

import logging

from tidmon.core.models.resources import Track, Video, Album, Playlist, Artist

logger = logging.getLogger(__name__)

# ============================================================
# LENGTH LIMITS
# ============================================================
MAX_ARTISTS_LEN    = 100   # tiddl parity (was 60)
MAX_TITLE_LEN      = 150   # tiddl parity (was 120)
MAX_FILENAME_BYTES = 250   # max bytes for full path component
MAX_COMPONENT_LEN  = 250   # alias used in sanitize_filename

# Single source of truth for the artist separator default.
# Used by generate_template_data, format_path, add_track_metadata, and
# all config.get("artist_separator", ...) call-sites.
DEFAULT_ARTIST_SEPARATOR = ", "
RESERVED_BYTE_COUNT = 50   # bytes reserved for downloader suffixes (.flac.part.<hash>)

# ============================================================
# Security options
# ============================================================
ASCII_ONLY = False

CHAR_TO_FULL_WIDTH = {
    '<': '＜', '>': '＞', ':': '：', '"': '＂',
    '/': '／', '\\': '＼', '|': '｜', '?': '？', '*': '＊',
}

_WIN_FORBIDDEN_RE  = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_DRIVE_RE          = re.compile(r"^[A-Za-z]:$")
_RESERVED_NAMES    = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}
_RE_NORMALIZE = re.compile(r'[\W_]+')
_NFD = unicodedata.normalize

_KEYWORDS_PATTERN = (
    r"f(?:ea)?t(?:\.|uring)?|with|w/|starring|guest(?: vocals:?)?|vocals?(?::| by)|"
    r"prod(?:\.|uced by)|(?:remix|edit|mix) by|"
    r"vs\.?|x|×|pres(?:en)?t(?:s|a|e)?|"
    r"collab(?:oration)?|"
    r"con|junto a|y|col(?:\.|aboraci[oó]n)?|invitado|voz(?: de)?|producido por|remix de|"
    r"mit|avec|et"
)

_RE_ANTI_FEAT = re.compile(
    r"(?:\s*(?:[\(\[\{])\s*"
    r"(?:" + _KEYWORDS_PATTERN + r")"
    r"\s+([^)\}\]]+?)\s*(?:[\)\]\}]))"
    r"|"
    r"(?:\s+[-\u2013]\s+\s*"
    r"(?:" + _KEYWORDS_PATTERN + r")"
    r"\s+(.*))",
    flags=re.IGNORECASE,
)


# ============================================================
# FIX 1 — remove_zalgo: script-aware combining-mark limits
# ============================================================

_COMMON_DIACRITICS = frozenset([
    0x0300, 0x0301, 0x0302, 0x0303, 0x0304, 0x0306, 0x0307, 0x0308,
    0x0309, 0x030A, 0x030B, 0x030C, 0x030F, 0x0311, 0x0323, 0x0327,
    0x0328, 0x031B,
])


def _script_of(ch: str) -> str:
    code = ord(ch)
    if code <= 0x024F or (0x1E00 <= code <= 0x1EFF) or (0x2C60 <= code <= 0x2C7F):
        return "latin"
    if 0x0370 <= code <= 0x03FF or 0x1F00 <= code <= 0x1FFF:
        return "greek"
    if 0x0400 <= code <= 0x052F or 0x2DE0 <= code <= 0x2DFF:
        return "cyrillic"
    if 0x0590 <= code <= 0x05FF or 0xFB1D <= code <= 0xFB4F:
        return "hebrew"
    if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F:
        return "arabic"
    if 0x0E00 <= code <= 0x0E7F:
        return "thai"
    if 0x0E80 <= code <= 0x0EFF:
        return "lao"
    if 0x0900 <= code <= 0x097F:
        return "devanagari"
    if 0x0980 <= code <= 0x09FF:
        return "bengali"
    if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
        return "japanese_kana"
    if 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F or 0xAC00 <= code <= 0xD7AF:
        return "korean"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        return "cjk"
    return "other"


_SCRIPT_MARK_LIMITS = {
    "latin": 2, "greek": 2, "cyrillic": 1,
    "hebrew": 3, "arabic": 3, "thai": 3, "lao": 3,
    "devanagari": 2, "bengali": 2, "japanese_kana": 1,
    "korean": 0, "cjk": 0, "other": 2,
}


def remove_zalgo(text: str) -> str:
    """Remove Zalgo stacking while preserving legitimate diacritics (script-aware)."""
    if not text:
        return ""

    s = unicodedata.normalize("NFC", str(text))
    if not s:
        return ""

    out = []
    current_script = "other"
    mark_count = 0
    mark_limit = 2
    seen_base = False

    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("M"):
            if not seen_base:
                continue
            mark_count += 1
            if mark_count <= mark_limit:
                if current_script in ("latin", "greek", "cyrillic"):
                    if ord(ch) in _COMMON_DIACRITICS:
                        out.append(ch)
                else:
                    out.append(ch)
        else:
            seen_base = True
            out.append(ch)
            current_script = _script_of(ch)
            mark_limit = _SCRIPT_MARK_LIMITS.get(current_script, 2)
            mark_count = 0

    result = "".join(out)

    # Emergency: if still mostly marks on a very short string, strip all
    total = len(result)
    if total > 0:
        remaining = sum(1 for c in result if unicodedata.category(c).startswith("M"))
        if total <= 4 and remaining > total * 0.5:
            result = "".join(c for c in result if not unicodedata.category(c).startswith("M"))

    return unicodedata.normalize("NFC", result)


# ============================================================
# FIX 2 — _generate_fallback_name for empty / junk strings
# ============================================================

def _extract_readable_parts(text: str, min_length: int = 2) -> list:
    if not text:
        return []
    parts = re.findall(r'[a-zA-Z0-9]+', text)
    return [p for p in parts if len(p) >= min_length]


def _generate_fallback_name(original: str = None, item_id: int = None) -> str:
    if original:
        parts = _extract_readable_parts(original, min_length=2)
        if parts:
            parts = sorted(parts, key=len, reverse=True)[:3]
            readable = '_'.join(parts)[:50]
            return f"{readable}_{item_id}" if item_id else readable
    return f"Item_{item_id}" if item_id else "Unknown"


# ============================================================
# Core string utilities
# ============================================================

def _truncate(s: str, max_len: int) -> str:
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    return s if len(s) <= max_len else s[:max_len]


def truncate_str_bytes(text: str, max_bytes: int = 240) -> str:
    b = str(text).encode("utf-8")
    if len(b) <= max_bytes:
        return text
    return b[:max_bytes].decode("utf-8", errors="ignore")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return _RE_NORMALIZE.sub("", text).lower()


def get_alpha_bucket(name: str) -> str:
    if not name:
        return "#"
    s = remove_zalgo(str(name).strip())
    if not s:
        return "#"
    ch = s[0].upper()
    decomposed = _NFD("NFD", ch)
    base = "".join(c for c in decomposed if unicodedata.category(c) != "Mn").upper()
    return base if ("A" <= base <= "Z") else "#"


# ============================================================
# FIX 3 — sanitize_filename: byte truncation + reserve + fallbacks
# FIX 4 — remove double Windows-char substitution
# FIX 5 — alnum ratio guard
# ============================================================

def sanitize_filename(
    s: str,
    item_id: Optional[int] = None,
    max_len: int = MAX_COMPONENT_LEN,
    reserve_bytes: int = 0,
) -> str:
    """
    Sanitize a single filename component.

    Args:
        s:             Raw string to sanitize.
        item_id:       Used in fallback names (e.g. "Song_12345").
        max_len:       Maximum byte length of the result.
        reserve_bytes: Bytes to reserve for downloader suffixes (.flac.part.<hash>).
                       Pass RESERVED_BYTE_COUNT (50) for filenames, 0 for folders.
    """
    if not s or not s.strip():
        return _generate_fallback_name(None, item_id)

    original_input = s

    # Unicode cleanup
    s = remove_zalgo(s)
    s = unicodedata.normalize("NFC", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cc", "Cf", "Cs"))

    # FIX 4: Apply full-width substitutions ONLY — no secondary regex wipe.
    # _WIN_FORBIDDEN_RE is NOT applied after this; it would destroy the full-width chars.
    for char, full_width in CHAR_TO_FULL_WIDTH.items():
        s = s.replace(char, full_width)

    if ASCII_ONLY:
        s = s.encode("ascii", "ignore").decode("ascii", "ignore")

    # Cosmetic cleanup
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'_+', '_', s)
    s = re.sub(r'\.+', '.', s)
    s = s.rstrip('. ')

    if not s:
        return _generate_fallback_name(original_input, item_id)

    if s == "#":
        return s

    total_chars = len(s)

    # FIX 5: Alnum ratio guard — strings that are almost entirely symbols get a fallback
    if total_chars > 3:
        alnum_count = sum(1 for c in s if c.isalnum())
        if alnum_count / total_chars < 0.15:
            return _generate_fallback_name(original_input, item_id)

        # Extra guard: long strings with no readable ASCII content
        if len(s.encode("utf-8")) > 60 and not _extract_readable_parts(s, min_length=2):
            return _generate_fallback_name(original_input, item_id)

    # Windows reserved names
    base_name = s.upper().split('.')[0].strip()
    if base_name in _RESERVED_NAMES:
        s = f"_{s}"

    # FIX 1 (critical): byte-aware truncation with suffix reservation
    effective_max = max(max_len - reserve_bytes, 20)
    return truncate_str_bytes(s, effective_max)


# ============================================================
# FIX 6 — _sanitize_segment: per-component byte limits
# ============================================================

def _sanitize_segment(
    segment: str,
    index: int,
    item_id: Optional[int] = None,
    max_len: int = MAX_COMPONENT_LEN,
    reserve_bytes: int = 0,
) -> str:
    s = (segment or "").strip()

    # Preserve leading dot (hidden files / version strings like ".1")
    leading_dot = ""
    if s.startswith("."):
        leading_dot = "."
        s = s[1:]

    # Preserve Windows drive letters (C:, D:, …) verbatim
    if index == 0 and _DRIVE_RE.match(s):
        return s.upper()

    effective_max = max_len - len(leading_dot)
    sanitized = sanitize_filename(s, item_id, max_len=effective_max, reserve_bytes=reserve_bytes)
    return leading_dot + sanitized


# ============================================================
# Templates
# ============================================================

class Explicit:
    def __init__(self, val):
        self.val = val

    def __format__(self, fmt):
        if not self.val:
            return ""
        if "shortparens" in fmt: return " (explicit)"
        if "parens"      in fmt: return " (Explicit)"
        if "upper"       in fmt: return "EXPLICIT" if "long" in fmt else "E"
        return "explicit" if "long" in fmt else "E"


class UserFormat:
    def __init__(self, val):
        self.val = val

    def __format__(self, fmt):
        return fmt if self.val else ""


# FIX 7 — Add safe_* variants to templates
@dataclass
class AlbumTemplate:
    id:           int
    title:        str
    safe_title:   str        # pre-sanitized for use in paths
    artist:       str
    safe_artist:  str
    artists:      str
    safe_artists: str
    date:         datetime
    explicit:     Explicit
    master:       UserFormat
    release:      str


@dataclass
class ItemTemplate:
    id:                   int
    title:                str
    safe_title:           str
    title_version:        str
    number:               int
    volume:               int
    version:              str
    copyright:            str
    bpm:                  int
    isrc:                 str
    quality:              str
    artist:               str
    safe_artist:          str
    artists:              str
    features:             str
    artists_with_features: str
    explicit:             Explicit
    dolby:                UserFormat
    releaseDate:          datetime
    streamStartDate:      datetime


@dataclass
class PlaylistTemplate:
    uuid:    str
    title:   str
    index:   int
    created: datetime
    updated: datetime


# ============================================================
# Main logic
# ============================================================

def parse_date_safe(date_str: Any) -> datetime:
    if not date_str:
        return datetime.min
    if isinstance(date_str, datetime):
        dt = date_str
    else:
        try:
            if len(str(date_str)) == 10 and '-' in str(date_str):
                dt = datetime.strptime(str(date_str), "%Y-%m-%d")
            else:
                dt = datetime.fromisoformat(str(date_str))
        except (ValueError, TypeError):
            return datetime.min
    return dt


def clean_track_title(track: Track) -> str:
    if not track or not track.title:
        return ""

    meta_artists = [a.name.strip().lower() for a in track.artists if a.name]
    meta_artists = [a for a in meta_artists if a]

    def is_known(name):
        n = name.strip().lower()
        if not n:
            return True
        if n in meta_artists:
            return True
        pattern = rf"\b{re.escape(n)}\b"
        for ma in meta_artists:
            if re.search(pattern, ma):
                return True
        return False

    def replacement(match):
        full_match = match.group(0)
        content = match.group(1) or match.group(2)
        if not content:
            return full_match
        parts = re.split(
            r"\s*(?:,|&|\+| and | y | et | und | con | with )\s*",
            content, flags=re.IGNORECASE,
        )
        unknown_parts = [p.strip() for p in parts if not is_known(p)]
        if not unknown_parts:
            return ""
        if len(unknown_parts) == len(parts):
            return full_match
        return full_match.replace(content, ", ".join(unknown_parts))

    return _RE_ANTI_FEAT.sub(replacement, track.title).strip()


def build_artist_string(
    track: Union[Track, Video],
    separator: str = DEFAULT_ARTIST_SEPARATOR,
) -> str:
    """Return a joined artist string for metadata tags.

    Sorts MAIN and FEATURED artists separately, then joins them with
    *separator*.  Falls back to all artists (unsorted by type) when no
    typed artists are found, and finally to the singular ``track.artist``
    field as a last resort.
    """
    artists_raw = track.artists or []
    m_arts = sorted([a.name for a in artists_raw if a.type == "MAIN"     and a.name])
    f_arts = sorted([a.name for a in artists_raw if a.type == "FEATURED" and a.name])
    if not m_arts and not f_arts:
        m_arts = sorted([a.name for a in artists_raw if a.name])
    if not m_arts and track.artist and track.artist.name:
        m_arts = [track.artist.name]
    return separator.join(m_arts + f_arts)


def generate_template_data(
    item:             Optional[Union[Track, Video]] = None,
    album:            Optional[Album]               = None,
    playlist:         Optional[Playlist]            = None,
    playlist_index:   int                           = 0,
    quality:          str                           = "",
    artist_separator: str                           = DEFAULT_ARTIST_SEPARATOR,
) -> dict:

    safe_file_len   = MAX_COMPONENT_LEN   # 250 bytes for filenames
    safe_folder_len = 150                 # 150 bytes for folder components

    item_tmpl = None
    if item:
        artists_raw = item.artists or []
        m_arts = sorted([a.name for a in artists_raw if a.type == "MAIN"     and a.name])
        f_arts = sorted([a.name for a in artists_raw if a.type == "FEATURED" and a.name])

        # Fallback: single artist without a type tag
        if not m_arts and len(artists_raw) == 1 and artists_raw[0].name:
            m_arts = [artists_raw[0].name]

        ver = (getattr(item, 'version', None) or "").strip()

        is_dolby = False
        if isinstance(item, Track) and item.media_metadata and item.media_metadata.tags:
            is_dolby = "DOLBY_ATMOS" in item.media_metadata.tags

        clean_title = clean_track_title(item)
        t_trunc  = _truncate(clean_title, MAX_TITLE_LEN)
        ver_str  = f" ({ver})" if ver else ""
        tv_trunc = _truncate(f"{t_trunc}{ver_str}", MAX_TITLE_LEN)
        af_trunc = _truncate(artist_separator.join(m_arts + f_arts), MAX_ARTISTS_LEN)

        art_name = (
            item.artist.name
            if item.artist and item.artist.name
            else (m_arts[0] if m_arts else "")
        )

        item_tmpl = ItemTemplate(
            id                   = item.id or 0,
            title                = t_trunc,
            safe_title           = sanitize_filename(t_trunc, item.id, max_len=safe_file_len),
            title_version        = tv_trunc,
            number               = getattr(item, 'track_number',  None) or 0,
            volume               = getattr(item, 'volume_number', None) or 0,
            version              = ver,
            copyright            = getattr(item, 'copyright', None) or "",
            bpm                  = getattr(item, 'bpm',        None) or 0,
            isrc                 = getattr(item, 'isrc',       None) or "",
            quality              = quality,
            artist               = art_name,
            safe_artist          = sanitize_filename(art_name, item.id, max_len=safe_folder_len),
            artists              = artist_separator.join(m_arts),
            features             = artist_separator.join(f_arts),
            artists_with_features= af_trunc,
            explicit             = Explicit(item.explicit),
            dolby                = UserFormat(is_dolby),
            releaseDate          = parse_date_safe(item.release_date),
            streamStartDate      = parse_date_safe(item.stream_start_date),
        )

    album_tmpl = None
    if album:
        d    = parse_date_safe(album.release_date)
        tags = (album.media_metadata.tags
                if album.media_metadata and album.media_metadata.tags else [])
        is_master = "HIRES_LOSSLESS" in tags and quality == "MAX"

        clean_album_title = album.title or ""
        clean_album_title = re.sub(
            r"\s*\(\s*(?:Explicit|E)\s*\)", "", clean_album_title, flags=re.IGNORECASE
        )

        album_artist_name = (
            album.artist.name if album.artist and album.artist.name else ""
        )
        alb_artists      = album.artists or []
        alb_main_artists = sorted([a.name for a in alb_artists if a.type == "MAIN" and a.name])

        album_tmpl = AlbumTemplate(
            id           = album.id or 0,
            title        = clean_album_title,
            safe_title   = sanitize_filename(clean_album_title, album.id, max_len=safe_folder_len),
            artist       = album_artist_name,
            safe_artist  = sanitize_filename(album_artist_name, album.id, max_len=safe_folder_len),
            artists      = ", ".join(alb_main_artists),
            safe_artists = sanitize_filename(", ".join(alb_main_artists), album.id, max_len=safe_folder_len),
            date         = d,
            explicit     = Explicit(album.explicit),
            master       = UserFormat(is_master),
            release      = album.type or "ALBUM",
        )
    elif item:
        # Fallback album for Music Videos or tracks without album context
        d        = parse_date_safe(item.release_date)
        art_name = item.artist.name if item.artist and item.artist.name else ""

        album_tmpl = AlbumTemplate(
            id           = 0,
            title        = item.title or "",
            safe_title   = sanitize_filename(item.title or "", 0, max_len=safe_folder_len),
            artist       = art_name,
            safe_artist  = sanitize_filename(art_name, 0, max_len=safe_folder_len),
            artists      = art_name,
            safe_artists = sanitize_filename(art_name, 0, max_len=safe_folder_len),
            date         = d,
            explicit     = Explicit(item.explicit),
            master       = UserFormat(False),
            release      = "SINGLE",
        )

    playlist_tmpl = None
    if playlist:
        c = parse_date_safe(playlist.created)
        u = parse_date_safe(playlist.last_updated)
        playlist_tmpl = PlaylistTemplate(
            uuid    = playlist.uuid,
            title   = playlist.title,
            index   = playlist_index,
            created = c,
            updated = u,
        )

    return {"item": item_tmpl, "album": album_tmpl, "playlist": playlist_tmpl}


def _normalize_initial_folder_component(component: str) -> str:
    if not component:
        return component
    comp = str(component).strip()
    if not comp or comp == "#":
        return "#"
    if len(comp) == 1:
        return get_alpha_bucket(comp)
    return component


# ============================================================
# FIX 8 — clean_filepath: per-segment byte limits
# ============================================================

def clean_filepath(fp: str) -> str:
    s = remove_zalgo(fp)
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ")

    is_unc = s.startswith("//") or s.startswith("\\\\")

    parts = re.split(r"[/\\]+", s)
    drive = None

    if parts:
        first = parts[0]
        if _DRIVE_RE.match(first):
            drive = first.upper()
            parts = parts[1:]
        elif parts[0]:
            parts[0] = _normalize_initial_folder_component(parts[0])

    parts = [p for p in parts if p]
    sanitized = []
    for idx, p in enumerate(parts):
        is_last  = (idx == len(parts) - 1)
        limit    = MAX_COMPONENT_LEN if is_last else 150   # folders capped at 150 bytes
        r_bytes  = RESERVED_BYTE_COUNT if is_last else 0
        sanitized.append(sanitize_filename(p, max_len=limit, reserve_bytes=r_bytes))

    path = "/".join(sanitized)

    if drive:
        path = f"{drive}{('/' + path) if path else ''}"
    if is_unc:
        path = "//" + path
    return path


# ============================================================
# FIX 9 — truncate_filepath_to_max: byte-correct length check
# ============================================================

def truncate_filepath_to_max(path: str, max_length: int = MAX_FILENAME_BYTES) -> str:
    # FIX: compare byte length, not character length (critical for CJK/emoji)
    if len(path.encode("utf-8")) <= max_length:
        return path

    m = re.match(r"^(.*[/\\])([^/\\]+)$", path)
    if not m:
        return truncate_str_bytes(path, max_length)

    dir_path, filename = m.group(1), m.group(2)
    if "." in filename:
        base, ext = filename.rsplit(".", 1)
        ext = f".{ext}"
    else:
        base, ext = filename, ""

    # FIX: use byte lengths for all measurements
    dir_len  = len(dir_path.encode("utf-8"))
    ext_len  = len(ext.encode("utf-8"))
    allowed  = max_length - dir_len - ext_len

    if allowed <= 0:
        return truncate_str_bytes(path, max_length)

    truncated_base = truncate_str_bytes(base, allowed)
    return f"{dir_path}{truncated_base}{ext}"


# ============================================================
# format_template — FIX 10: per-segment limits + FIX 11: sanitized disc folder
# ============================================================

def format_template(
    template:         str,
    item:             Optional[Union[Track, Video]] = None,
    album:            Optional[Album]               = None,
    playlist:         Optional[Playlist]            = None,
    playlist_index:   int                           = 0,
    quality:          str                           = "",
    with_asterisk_ext: bool                         = True,
    artist_separator: str                           = DEFAULT_ARTIST_SEPARATOR,
    **extra,
) -> str:

    template  = template.strip().lstrip('\ufeff').replace("\\", "/")
    base_data = generate_template_data(item, album, playlist, playlist_index, quality, artist_separator)

    aliases: dict = {}
    if item and base_data.get("item"):
        aliases["title"]          = base_data["item"].title
        aliases["artist"]         = base_data["item"].artist
        aliases["artist_initials"] = get_alpha_bucket(base_data["item"].artist)

    if album and base_data.get("album"):
        aliases["albumartist"]    = base_data["album"].artist
        aliases["release_date"]   = base_data["album"].date
        aliases["artist_initials"] = get_alpha_bucket(base_data["album"].artist)

    data = {**base_data, **extra, **aliases, "now": datetime.now(), "quality": quality}

    # Determine item_id for fallback names in sanitize_filename
    current_id = None
    if item:
        current_id = getattr(item, "id", None)
    if not current_id and album:
        current_id = getattr(album, "id", None)

    parts = template.split("/")
    rendered_parts = []

    is_unc = template.startswith("//") or template.startswith("\\\\")
    if is_unc:
        parts = [p for p in parts if p]

    for idx, part in enumerate(parts):
        try:
            rendered = part.format(**data)
        except Exception:
            rendered = part.replace(":", "-").replace("{", "(").replace("}", ")")

        seg_idx = idx if not is_unc else idx + 99
        # FIX 10: pass per-segment limits — folders 150 bytes, filename 250 bytes
        is_last  = (idx == len(parts) - 1)
        limit    = MAX_COMPONENT_LEN if is_last else 150
        r_bytes  = RESERVED_BYTE_COUNT if is_last else 0
        rendered_parts.append(_sanitize_segment(rendered, seg_idx, current_id, limit, r_bytes))

    # Auto-inject Disc folder for multi-volume albums
    if item and album and (album.number_of_volumes or 0) > 1:
        if "{item.volume}" not in template:
            vol = item.volume_number or 1
            # FIX 11: sanitize the disc folder string
            disc_part = _sanitize_segment(f"Disc {vol}", 0, current_id, max_len=150, reserve_bytes=0)
            if len(rendered_parts) >= 1:
                rendered_parts.insert(-1, disc_part)
            else:
                rendered_parts.insert(0, disc_part)

    path = "/".join(rendered_parts)
    if is_unc:
        path = "//" + path

    path = clean_filepath(path)
    path = truncate_filepath_to_max(path, MAX_FILENAME_BYTES)

    if with_asterisk_ext:
        path += ".*"

    return path