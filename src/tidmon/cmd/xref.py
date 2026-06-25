"""
tidmon xref — Cross-platform artist ID reference.

Builds and maintains a cross-reference table mapping TIDAL artist IDs
to Qobuz, Apple Music, Deezer and MusicBrainz IDs.

Sources (in order of preference):
  1. odesli DB  (~/.odesli/music.db)  — already has Apple/Deezer/Spotify
  2. MusicBrainz API                  — MBID + URL relationships → Qobuz/Deezer/Apple
  3. Qobuz search API                 — fallback name search for missing Qobuz IDs
"""
from __future__ import annotations

import csv
import logging
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Optional

import click
from rich import box
from rich.console import Console
from rich.table import Table

from tidmon.core.db import Database

logger = logging.getLogger(__name__)
console = Console(highlight=False)


# ── xref DB helpers ───────────────────────────────────────────────────────────

XREF_TABLE = """
CREATE TABLE IF NOT EXISTS artist_xref (
    tidal_id        INTEGER PRIMARY KEY,
    artist_name     TEXT NOT NULL,
    mbid            TEXT,
    qobuz_id        TEXT,
    apple_music_id  TEXT,
    deezer_id       TEXT,
    spotify_id      TEXT,
    updated_at      TEXT
)
"""


def _get_xref_db() -> Path:
    from tidmon.core.utils.startup import get_appdata_dir
    return get_appdata_dir() / "xref.db"


def _open_xref(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute(XREF_TABLE)
    conn.commit()
    return conn


def _normalize(name: str) -> str:
    """Lowercase, strip accents, remove punctuation — for fuzzy name matching."""
    nfkd = unicodedata.normalize("NFKD", name.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip()


# ── MusicBrainz helpers ───────────────────────────────────────────────────────

_MB_BASE = "https://musicbrainz.org/ws/2"
_MB_HEADERS = {"User-Agent": "tidmon-xref/1.0 (https://github.com/np3ir/tidmon-cli)"}
_MB_PLATFORM_MAP = {
    "qobuz.com":      "qobuz",
    "tidal.com":      "tidal",
    "music.apple.com":"apple_music",
    "deezer.com":     "deezer",
    "open.spotify.com": "spotify",
}


def _mb_get(path: str, params: dict) -> Optional[dict]:
    import urllib.request, urllib.parse, json
    params["fmt"] = "json"
    url = f"{_MB_BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_MB_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.debug("MB request failed %s: %s", url, e)
        return None


def _mb_search_artist(name: str) -> Optional[str]:
    """Return best MBID for artist name, or None."""
    data = _mb_get("artist", {"query": f'artist:"{name}"', "limit": 1})
    if data and data.get("artists"):
        return data["artists"][0].get("id")
    return None


def _mb_url_relations(mbid: str) -> dict:
    """Return dict of platform → platform_id from MB URL relationships."""
    data = _mb_get(f"artist/{mbid}", {"inc": "url-rels"})
    result = {}
    if not data:
        return result
    for rel in data.get("relations", []):
        url = rel.get("url", {}).get("resource", "")
        for domain, key in _MB_PLATFORM_MAP.items():
            if domain in url:
                parts = [p for p in url.rstrip("/").split("/") if p]
                if key in ("apple_music", "deezer"):
                    # IDs are always numeric; URLs may contain country codes or slugs
                    # e.g. https://music.apple.com/us/artist/name/123456789
                    # e.g. https://www.deezer.com/artist/123456
                    numeric_parts = [p for p in parts if p.isdigit()]
                    pid = numeric_parts[-1] if numeric_parts else None
                elif key == "qobuz":
                    # https://www.qobuz.com/us-en/interpreter/name/123456
                    numeric_parts = [p for p in parts if p.isdigit()]
                    pid = numeric_parts[-1] if numeric_parts else (parts[-1] if parts else None)
                else:
                    pid = parts[-1] if parts else None
                if pid:
                    result[key] = pid
                break
    return result


# ── Qobuz search helper ───────────────────────────────────────────────────────

def _qobuz_search_artist(name: str, app_id: str, token: str) -> Optional[str]:
    """Search Qobuz API for artist by name, return artist_id or None."""
    import urllib.request, urllib.parse, json
    params = {"query": name, "limit": "5", "app_id": app_id}
    url = f"https://www.qobuz.com/api.json/0.2/artist/search?{urllib.parse.urlencode(params)}"
    headers = {"X-User-Auth-Token": token, "X-App-Id": app_id}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        artists = data.get("artists", {}).get("items", [])
        if not artists:
            return None
        # Pick best match by normalized name
        norm_name = _normalize(name)
        for a in artists:
            if _normalize(a.get("name", "")) == norm_name:
                return str(a["id"])
        # Fallback: first result
        return str(artists[0]["id"])
    except Exception as e:
        logger.debug("Qobuz search failed for %r: %s", name, e)
        return None


# ── Core Xref class ───────────────────────────────────────────────────────────

class Xref:
    def __init__(self):
        self.xref_path = _get_xref_db()
        self.xref = _open_xref(self.xref_path)
        self.db = Database()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def close(self):
        self.xref.close()
        self.db.close()

    # ── enrich ────────────────────────────────────────────────────────────────

    def enrich(
        self,
        odesli_db: Optional[Path],
        use_mb: bool,
        use_qobuz: bool,
        qobuz_app_id: Optional[str],
        qobuz_token: Optional[str],
        limit: Optional[int],
        mb_delay: float,
        qobuz_delay: float,
    ):
        artists = self.db.get_all_artists()
        if limit:
            artists = artists[:limit]

        total = len(artists)
        console.print(f"\n  [bold cyan]tidmon xref enrich[/] - {total:,} artistas")

        # Step 1: seed xref table with all tidmon artists
        now = _now()
        with self.xref:
            for a in artists:
                self.xref.execute("""
                    INSERT OR IGNORE INTO artist_xref (tidal_id, artist_name, updated_at)
                    VALUES (?, ?, ?)
                """, (a["artist_id"], a["artist_name"], now))
        console.print(f"  [green]OK[/] Artistas seedeados en xref.db")

        # Step 2: enrich from odesli
        if odesli_db is None:
            odesli_db = Path.home() / ".odesli" / "music.db"
        if odesli_db.exists():
            self._enrich_from_odesli(odesli_db)
        else:
            console.print(f"  [yellow]![/] odesli DB no encontrada: {odesli_db}")

        # Step 3: MusicBrainz for missing IDs
        if use_mb:
            self._enrich_from_mb(mb_delay)

        # Step 4: Qobuz search for remaining missing Qobuz IDs
        if use_qobuz:
            if not qobuz_app_id or not qobuz_token:
                console.print("  [yellow]![/] --qobuz-app-id y --qobuz-token requeridos para Qobuz search")
            else:
                self._enrich_qobuz_search(qobuz_app_id, qobuz_token, qobuz_delay)

        self._print_coverage()

    def _enrich_from_odesli(self, odesli_db: Path):
        console.print(f"  [cyan]>>[/] Enriqueciendo desde odesli...")
        oc = sqlite3.connect(str(odesli_db))
        oc.row_factory = sqlite3.Row
        odesli_rows = oc.execute("""
            SELECT a.name, ap.platform, ap.platform_id
            FROM artist_platforms ap
            JOIN artists a ON a.id = ap.artist_id
            WHERE ap.platform IN ('TIDAL','Qobuz','Apple Music','Deezer','Spotify')
        """).fetchall()
        oc.close()

        # Build lookup: normalized_name → {platform: id}
        by_name: dict[str, dict] = {}
        for r in odesli_rows:
            key = _normalize(r["name"])
            by_name.setdefault(key, {})[r["platform"]] = r["platform_id"]

        # Also build tidal_id → odesli platforms directly
        tidal_rows = [r for r in odesli_rows if r["platform"] == "TIDAL"]
        by_tidal: dict[str, dict] = {}
        for r in tidal_rows:
            # need all platforms for this artist
            pass  # covered by by_name lookup below

        rows = self.xref.execute("SELECT tidal_id, artist_name FROM artist_xref").fetchall()
        updated = 0
        for row in rows:
            key = _normalize(row["artist_name"])
            platforms = by_name.get(key, {})
            if not platforms:
                continue
            self.xref.execute("""
                UPDATE artist_xref SET
                    qobuz_id       = COALESCE(qobuz_id,       ?),
                    apple_music_id = COALESCE(apple_music_id, ?),
                    deezer_id      = COALESCE(deezer_id,      ?),
                    spotify_id     = COALESCE(spotify_id,     ?),
                    updated_at     = ?
                WHERE tidal_id = ?
            """, (
                platforms.get("Qobuz"),
                platforms.get("Apple Music"),
                platforms.get("Deezer"),
                platforms.get("Spotify"),
                _now(),
                row["tidal_id"],
            ))
            updated += 1

        self.xref.commit()
        console.print(f"  [green]OK[/] odesli: {updated:,} artistas actualizados")

    def _enrich_from_mb(self, delay: float):
        # Only process artists missing at least one platform ID
        rows = self.xref.execute("""
            SELECT tidal_id, artist_name, mbid
            FROM artist_xref
            WHERE mbid IS NULL
               OR qobuz_id IS NULL
               OR apple_music_id IS NULL
               OR deezer_id IS NULL
        """).fetchall()
        total = len(rows)
        console.print(f"  [cyan]>>[/] MusicBrainz: {total:,} artistas sin cobertura completa...")

        found_mbid = 0
        found_ids = 0
        for i, row in enumerate(rows, 1):
            name = row["artist_name"]
            mbid = row["mbid"]

            if not mbid:
                mbid = _mb_search_artist(name)
                time.sleep(delay)

            if not mbid:
                continue

            relations = _mb_url_relations(mbid)
            time.sleep(delay)

            if not relations and not mbid:
                continue

            self.xref.execute("""
                UPDATE artist_xref SET
                    mbid           = ?,
                    qobuz_id       = COALESCE(qobuz_id,       ?),
                    apple_music_id = COALESCE(apple_music_id, ?),
                    deezer_id      = COALESCE(deezer_id,      ?),
                    spotify_id     = COALESCE(spotify_id,     ?),
                    updated_at     = ?
                WHERE tidal_id = ?
            """, (
                mbid,
                relations.get("qobuz"),
                relations.get("apple_music"),
                relations.get("deezer"),
                relations.get("spotify"),
                _now(),
                row["tidal_id"],
            ))
            found_mbid += 1
            if relations:
                found_ids += 1

            if i % 500 == 0:
                self.xref.commit()
                console.print(f"    {i:,}/{total:,} procesados...")

        self.xref.commit()
        console.print(f"  [green]OK[/] MusicBrainz: {found_mbid:,} MBIDs, {found_ids:,} con URL relations")

    def _enrich_qobuz_search(self, app_id: str, token: str, delay: float):
        rows = self.xref.execute("""
            SELECT tidal_id, artist_name FROM artist_xref WHERE qobuz_id IS NULL
        """).fetchall()
        total = len(rows)
        console.print(f"  [cyan]>>[/] Qobuz search: {total:,} artistas sin Qobuz ID...")

        found = 0
        for i, row in enumerate(rows, 1):
            qid = _qobuz_search_artist(row["artist_name"], app_id, token)
            if qid:
                self.xref.execute("""
                    UPDATE artist_xref SET qobuz_id = ?, updated_at = ? WHERE tidal_id = ?
                """, (qid, _now(), row["tidal_id"]))
                found += 1
            time.sleep(delay)

            if i % 200 == 0:
                self.xref.commit()
                console.print(f"    {i:,}/{total:,} - encontrados: {found:,}")

        self.xref.commit()
        console.print(f"  [green]OK[/] Qobuz search: {found:,} IDs encontrados de {total:,}")

    # ── export ────────────────────────────────────────────────────────────────

    def export(self, output: Path, platform_filter: list[str]):
        rows = self.xref.execute("""
            SELECT tidal_id, artist_name, mbid, qobuz_id, apple_music_id, deezer_id, spotify_id
            FROM artist_xref ORDER BY artist_name COLLATE NOCASE
        """).fetchall()

        headers = ["artist_name", "tidal_id"]
        if not platform_filter or "qobuz" in platform_filter:
            headers.append("qobuz_id")
        if not platform_filter or "apple" in platform_filter:
            headers.append("apple_music_id")
        if not platform_filter or "deezer" in platform_filter:
            headers.append("deezer_id")
        if not platform_filter or "spotify" in platform_filter:
            headers.append("spotify_id")
        if not platform_filter or "mb" in platform_filter:
            headers.append("mbid")

        with open(output, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(headers)
            for r in rows:
                row_data = {"artist_name": r["artist_name"], "tidal_id": r["tidal_id"],
                            "qobuz_id": r["qobuz_id"] or "", "apple_music_id": r["apple_music_id"] or "",
                            "deezer_id": r["deezer_id"] or "", "spotify_id": r["spotify_id"] or "",
                            "mbid": r["mbid"] or ""}
                w.writerow([row_data[h] for h in headers])

        console.print(f"  [green]OK[/] {len(rows):,} artistas exportados a [bold]{output}[/]")

    # ── show ──────────────────────────────────────────────────────────────────

    def show(self):
        self._print_coverage()

    def _print_coverage(self):
        total = self.xref.execute("SELECT COUNT(*) FROM artist_xref").fetchone()[0]
        if total == 0:
            console.print("  [yellow]xref.db vacia - corre [bold]tidmon xref enrich[/] primero")
            return

        platforms = [
            ("Qobuz",       "qobuz_id"),
            ("Apple Music", "apple_music_id"),
            ("Deezer",      "deezer_id"),
            ("Spotify",     "spotify_id"),
            ("MusicBrainz", "mbid"),
        ]

        table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
        table.add_column("Plataforma", style="bold")
        table.add_column("Con ID", justify="right")
        table.add_column("Sin ID", justify="right")
        table.add_column("Cobertura", justify="right")

        for label, col in platforms:
            n = self.xref.execute(f"SELECT COUNT(*) FROM artist_xref WHERE {col} IS NOT NULL").fetchone()[0]
            missing = total - n
            pct = f"{n/total*100:.1f}%"
            table.add_row(label, f"{n:,}", f"{missing:,}", pct)

        console.print(f"\n  [bold]xref.db[/] - {total:,} artistas (TIDAL)\n")
        console.print(table)
        console.print(f"  DB: [dim]{self.xref_path}[/]\n")


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
