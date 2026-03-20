import re
import time
import random
import logging
import requests as _requests
from typing import Literal, TypeAlias, Any, List, Optional

from requests.exceptions import (
    HTTPError, ConnectionError, Timeout, ChunkedEncodingError, ReadTimeout
)

from tidmon.core.client import TidalClientImproved
from tidmon.core.exceptions import ApiError
from tidmon.core.models.base import (
    AlbumItems, ArtistAlbumsItems, ArtistVideosItems, PlaylistItems,
    TrackLyrics, Search, Favorites, MixItems, AlbumItemsCredits
)
from tidmon.core.models.resources import (
    Album, Artist, Playlist, Track, Video,
    ArtistTopTracks, TrackCredits, TrackMix, VideoStream, TrackStream,
    ArtistBio, ArtistLinks
)

ID: TypeAlias = str | int
log = logging.getLogger(__name__)


class Limits:
    ARTIST_ALBUMS_MAX = 100
    ALBUM_ITEMS_MAX = 100
    PLAYLIST_ITEMS_MAX = 100
    ARTIST_VIDEOS_MAX = 100


class TidalAPI:
    """TIDAL API client using TidalClientImproved and Pydantic models."""
    client: TidalClientImproved
    user_id: str
    country_code: str

    def __init__(self, client: TidalClientImproved, user_id: str, country_code: str) -> None:
        self.client = client
        self.user_id = user_id
        self.country_code = country_code
        self._rate_limit_delay = 0.0

    def _fetch_with_retry(self, *args: Any, max_retries: int = 10, **kwargs: Any):
        if self._rate_limit_delay > 0:
            time.sleep(self._rate_limit_delay)

        attempt = 0
        base_backoff = 5
        max_backoff = 60

        while True:
            try:
                res = self.client.fetch(*args, **kwargs)
                if self._rate_limit_delay > 0:
                    self._rate_limit_delay = max(0.0, self._rate_limit_delay - 0.1)
                return res

            except Exception as e:
                is_net = isinstance(e, (ConnectionError, Timeout, ReadTimeout, ChunkedEncodingError))
                is_http = False
                status = None
                retry_head = None

                if isinstance(e, HTTPError) and getattr(e, "response", None):
                    status = e.response.status_code
                    retry_head = e.response.headers.get("Retry-After")
                elif isinstance(e, ApiError):
                    status = e.status

                if status:
                    if status in [401, 403]:
                        # client.fetch() already handles the full refresh cycle
                        # (on_token_expiry → retry with _refreshed=True). By the
                        # time the exception reaches here, the refresh has already
                        # been attempted. Re-trying here would double-refresh and
                        # produce spurious TOKEN ERROR logs. Raise immediately.
                        # Persistent 401 after a successful refresh = content
                        # restriction (geo-block, subscription tier), not a token
                        # issue — get_track_stream will return None gracefully.
                        log.debug(f"Received {status} — content not available or token issue already handled by client.")
                        raise e

                    if status in [406, 451]:
                        log.warning(f"Geo-blocked content ({status}). Skipping...")
                        raise e
                    if status in [400, 404]:
                        log.debug(f"Content not found ({status}). Skipping...")
                        raise e

                    if status in [429, 500, 502, 503, 504]:
                        is_http = True
                        if status == 429:
                            self._rate_limit_delay = min(5.0, self._rate_limit_delay + 1.0)
                elif "429" in str(e):
                    is_http = True
                    self._rate_limit_delay = min(5.0, self._rate_limit_delay + 1.0)

                if not is_net and not is_http:
                    raise e

                attempt += 1
                if attempt > max_retries:
                    log.error(f"SKIPPING: Failed {max_retries} times.")
                    raise e

                wait = 0
                if retry_head:
                    try:
                        wait = int(retry_head)
                    except Exception:
                        wait = 0
                    log.warning(f"Mandatory wait from API: {wait}s.")
                elif is_net:
                    wait = 10
                    log.warning("No connection. Retrying in 10s...")
                elif is_http:
                    if wait <= 0:
                        wait = min(base_backoff * (2 ** (attempt - 1)), max_backoff)
                    if status in [500, 502, 503, 504]:
                        log.warning(f"Server Error ({status}). Retrying in {wait:.0f}s...")
                    else:
                        status_display = status if status is not None else "429/Limit"
                        log.warning(f"API rate limit pause ({status_display})... {wait:.0f}s")

                time.sleep(wait + random.uniform(1, 3))
                continue

    # ── Albums ───────────────────────────────────────────────────────────────

    def get_album(self, album_id: ID) -> Optional[Album]:
        try:
            return self._fetch_with_retry(
                Album, f"albums/{album_id}", {"countryCode": self.country_code}
            )
        except Exception:
            pass
        log.debug(f"v1 get_album failed for {album_id}, trying v2 fallback...")
        return self._get_album_v2(album_id)

    def get_album_tracks(self, album_id: int) -> List[Track]:
        all_tracks = []
        offset = 0
        limit = Limits.ALBUM_ITEMS_MAX

        while True:
            try:
                params = {'limit': limit, 'offset': offset, 'countryCode': self.country_code}
                result = self._fetch_with_retry(AlbumItems, f'albums/{album_id}/items', params=params)
                if not result or not result.items:
                    break
                all_tracks.extend(result.items)
                if len(all_tracks) >= result.total_number_of_items:
                    break
                offset += len(result.items)
                if len(result.items) < limit:
                    break
            except Exception:
                break

        if not all_tracks:
            log.debug(f"v1 returned no tracks for album {album_id}, trying v2 fallback...")
            v2_tracks = self._get_album_tracks_v2(album_id)
            if v2_tracks:
                log.info(f"v2 fallback found {len(v2_tracks)} track(s) for album {album_id}")
            return v2_tracks
        return all_tracks

    # ── Artists ───────────────────────────────────────────────────────────────

    def get_artist(self, artist_id: ID) -> Optional[Artist]:
        try:
            return self._fetch_with_retry(
                Artist, f"artists/{artist_id}", {"countryCode": self.country_code}
            )
        except Exception:
            pass
        # v1 failed — try openapi.tidal.com v2
        log.debug(f"v1 get_artist failed for {artist_id}, trying v2 fallback...")
        return self._get_artist_v2(artist_id)

    def get_artist_albums(self, artist_id: int) -> Optional[List[Album]]:
        all_items = []
        seen_ids: set = set()
        any_success = False

        for f in ["ALBUMS", "EPSANDSINGLES", "COMPILATIONS"]:
            offset = 0
            filter_success = False
            while True:
                try:
                    params = {
                        'limit': Limits.ARTIST_ALBUMS_MAX,
                        'offset': offset,
                        'filter': f,
                        'countryCode': self.country_code
                    }
                    result = self._fetch_with_retry(
                        ArtistAlbumsItems, f'artists/{artist_id}/albums', params=params
                    )
                    filter_success = True
                    if not result or not result.items:
                        break

                    for item in result.items:
                        if item.id not in seen_ids:
                            all_items.append(item)
                            seen_ids.add(item.id)

                    if (offset + len(result.items)) >= result.total_number_of_items:
                        break
                    offset += len(result.items)
                except Exception:
                    break

            if filter_success:
                any_success = True

        # v1 succeeded but returned nothing — try openapi.tidal.com v2 as fallback
        if any_success and not all_items:
            log.debug(f"v1 returned no albums for artist {artist_id}, trying v2 fallback...")
            v2_albums = self._get_artist_albums_v2(artist_id)
            if v2_albums:
                log.info(f"v2 fallback found {len(v2_albums)} release(s) for artist {artist_id}")
            return v2_albums

        return all_items if any_success else None

    # ── openapi.tidal.com/v2 helpers ─────────────────────────────────────────

    _V2_BASE = "https://openapi.tidal.com/v2"

    def _v2_get(self, endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
        """GET request to openapi.tidal.com/v2. Returns parsed JSON body or None."""
        bearer = self.client.session.headers.get("Authorization", "")
        merged = {"countryCode": self.country_code, **(params or {})}
        try:
            resp = _requests.get(
                f"{self._V2_BASE}/{endpoint}",
                headers={"Authorization": bearer, "Accept": "application/vnd.api+json"},
                params=merged,
                timeout=15,
            )
            if resp.status_code != 200:
                log.debug(f"v2 GET {endpoint} returned {resp.status_code}")
                return None
            return resp.json()
        except Exception as e:
            log.debug(f"v2 GET {endpoint} error: {e}")
            return None

    def _get_artist_v2(self, artist_id: ID) -> Optional[Artist]:
        """Fetch a single artist from openapi.tidal.com/v2."""
        body = self._v2_get(f"artists/{artist_id}")
        if not body:
            return None
        data = body.get("data", {})
        attrs = data.get("attributes", {})
        try:
            # v2 picture is a list of {url, width, height}; keep only the UUID-like
            # path so callers that expect a v1 UUID string don't break entirely.
            pictures = attrs.get("picture", []) or []
            picture = pictures[0].get("url") if pictures else None
            return Artist(
                id=int(data["id"]),
                name=attrs.get("name", "Unknown"),
                popularity=attrs.get("popularity"),
                picture=picture,
            )
        except Exception as e:
            log.debug(f"v2 artist parse error for id={artist_id}: {e}")
            return None

    def _get_artist_albums_v2(self, artist_id: int) -> List[Album]:
        """Fallback: fetch artist albums from openapi.tidal.com/v2 (JSON:API).

        Used when the v1 API returns an empty list for an artist that visibly
        has releases on the TIDAL web player (e.g. newly-onboarded artists
        not yet indexed on the v1 catalogue endpoint).
        """
        # Step 1 — collect album IDs via cursor-paginated relationship endpoint
        album_ids: list = []
        cursor: Optional[str] = None
        while True:
            params: dict = {}
            if cursor:
                params["page[cursor]"] = cursor
            body = self._v2_get(f"artists/{artist_id}/relationships/albums", params)
            if not body:
                break
            items = body.get("data", [])
            if not items:
                break
            album_ids.extend(item["id"] for item in items)
            cursor = body.get("meta", {}).get("nextCursor")
            if not cursor:
                break

        if not album_ids:
            return []

        # Step 2 — fetch album details in batches of 20
        albums: List[Album] = []
        batch_size = 20
        for i in range(0, len(album_ids), batch_size):
            batch = album_ids[i:i + batch_size]
            body = self._v2_get("albums", {"filter[id]": ",".join(str(aid) for aid in batch)})
            if not body:
                continue
            for item in body.get("data", []):
                attrs = item.get("attributes", {})
                try:
                    album = Album(
                        id=int(item["id"]),
                        title=attrs.get("title", "Unknown"),
                        number_of_tracks=attrs.get("numberOfItems"),
                        release_date=attrs.get("releaseDate"),
                        type=attrs.get("albumType"),
                        explicit=attrs.get("explicit"),
                    )
                    if album.id not in {a.id for a in albums}:
                        albums.append(album)
                except Exception as e:
                    log.debug(f"v2 album parse error for id={item.get('id')}: {e}")

        return albums

    # ── openapi.tidal.com/v2 internal utilities ───────────────────────────────

    @staticmethod
    def _iso_to_sec(s: Optional[str]) -> Optional[int]:
        """Convert ISO 8601 duration string (e.g. 'PT2M24S') to integer seconds."""
        if not s:
            return None
        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', s)
        if not m:
            return None
        h, mi, sec = m.groups()
        return int(float(h or 0)) * 3600 + int(float(mi or 0)) * 60 + int(float(sec or 0))

    @staticmethod
    def _v2_audio_quality(media_tags: list) -> Optional[str]:
        """Derive an audioQuality string from v2 mediaTags list."""
        if "HIRES_LOSSLESS" in media_tags:
            return "HI_RES_LOSSLESS"
        if "LOSSLESS" in media_tags:
            return "LOSSLESS"
        if media_tags:
            return "HIGH"
        return None

    def _get_album_v2(self, album_id: ID) -> Optional[Album]:
        """Fallback: fetch a single album from openapi.tidal.com/v2."""
        body = self._v2_get(f"albums/{album_id}")
        if not body:
            return None
        data = body.get("data", {})
        attrs = data.get("attributes", {})
        try:
            return Album(
                id=int(data["id"]),
                title=attrs.get("title", "Unknown"),
                number_of_tracks=attrs.get("numberOfItems"),
                number_of_volumes=attrs.get("numberOfVolumes"),
                duration=self._iso_to_sec(attrs.get("duration")),
                release_date=attrs.get("releaseDate"),
                type=attrs.get("albumType") or attrs.get("type"),
                explicit=attrs.get("explicit"),
                audio_quality=self._v2_audio_quality(attrs.get("mediaTags") or []),
                copyright=(attrs.get("copyright") or {}).get("text"),
            )
        except Exception as e:
            log.debug(f"v2 album parse error for id={album_id}: {e}")
            return None

    def _get_album_tracks_v2(self, album_id: int) -> List[Track]:
        """Fallback: fetch album tracks from openapi.tidal.com/v2 (JSON:API).

        Collects track IDs from the cursor-paginated /albums/{id}/relationships/items
        endpoint, then batch-fetches full track data from /tracks?filter[id]=...
        """
        # Step 1 — collect track IDs via cursor-paginated items relationship
        track_ids: list = []
        cursor: Optional[str] = None
        while True:
            params: dict = {}
            if cursor:
                params["page[cursor]"] = cursor
            body = self._v2_get(f"albums/{album_id}/relationships/items", params)
            if not body:
                break
            items = body.get("data", [])
            if not items:
                break
            # relationship items have type "tracks" or "videos"; keep only tracks
            track_ids.extend(item["id"] for item in items if item.get("type") == "tracks")
            cursor = body.get("meta", {}).get("nextCursor")
            if not cursor:
                break

        if not track_ids:
            return []

        # Step 2 — fetch track details in batches of 20
        tracks: List[Track] = []
        seen: set = set()
        for i in range(0, len(track_ids), 20):
            batch = track_ids[i:i + 20]
            body = self._v2_get("tracks", {"filter[id]": ",".join(str(tid) for tid in batch)})
            if not body:
                continue
            for item in body.get("data", []):
                attrs = item.get("attributes", {})
                tid = int(item["id"])
                if tid in seen:
                    continue
                seen.add(tid)
                try:
                    bpm_raw = attrs.get("bpm")
                    tracks.append(Track(
                        id=tid,
                        title=attrs.get("title", "Unknown"),
                        duration=self._iso_to_sec(attrs.get("duration")),
                        isrc=attrs.get("isrc"),
                        explicit=attrs.get("explicit"),
                        bpm=int(round(bpm_raw)) if bpm_raw is not None else None,
                        version=attrs.get("version"),
                        copyright=(attrs.get("copyright") or {}).get("text"),
                        audio_quality=self._v2_audio_quality(attrs.get("mediaTags") or []),
                    ))
                except Exception as e:
                    log.debug(f"v2 track parse error for id={tid}: {e}")
        return tracks

    def _get_artist_videos_v2(self, artist_id: ID) -> List[Video]:
        """Fallback: fetch artist videos from openapi.tidal.com/v2 (JSON:API)."""
        # Step 1 — collect video IDs via cursor-paginated relationship
        video_ids: list = []
        cursor: Optional[str] = None
        while True:
            params: dict = {}
            if cursor:
                params["page[cursor]"] = cursor
            body = self._v2_get(f"artists/{artist_id}/relationships/videos", params)
            if not body:
                break
            items = body.get("data", [])
            if not items:
                break
            video_ids.extend(item["id"] for item in items)
            cursor = body.get("meta", {}).get("nextCursor")
            if not cursor:
                break

        if not video_ids:
            return []

        # Step 2 — fetch video details in batches of 20
        videos: List[Video] = []
        seen: set = set()
        for i in range(0, len(video_ids), 20):
            batch = video_ids[i:i + 20]
            body = self._v2_get("videos", {"filter[id]": ",".join(str(vid) for vid in batch)})
            if not body:
                continue
            for item in body.get("data", []):
                attrs = item.get("attributes", {})
                vid = int(item["id"])
                if vid in seen:
                    continue
                seen.add(vid)
                try:
                    videos.append(Video(
                        id=vid,
                        title=attrs.get("title", "Unknown"),
                        duration=self._iso_to_sec(attrs.get("duration")),
                        explicit=attrs.get("explicit"),
                        releaseDate=attrs.get("releaseDate"),
                    ))
                except Exception as e:
                    log.debug(f"v2 video parse error for id={vid}: {e}")
        return videos

    def _get_track_v2(self, track_id: ID) -> Optional[Track]:
        """Fallback: fetch a single track from openapi.tidal.com/v2."""
        body = self._v2_get(f"tracks/{track_id}")
        if not body:
            return None
        data = body.get("data", {})
        attrs = data.get("attributes", {})
        try:
            bpm_raw = attrs.get("bpm")
            return Track(
                id=int(data["id"]),
                title=attrs.get("title", "Unknown"),
                duration=self._iso_to_sec(attrs.get("duration")),
                isrc=attrs.get("isrc"),
                explicit=attrs.get("explicit"),
                bpm=int(round(bpm_raw)) if bpm_raw is not None else None,
                version=attrs.get("version"),
                copyright=(attrs.get("copyright") or {}).get("text"),
                audio_quality=self._v2_audio_quality(attrs.get("mediaTags") or []),
            )
        except Exception as e:
            log.debug(f"v2 track parse error for id={track_id}: {e}")
            return None

    def _get_track_lyrics_v2(self, track_id: ID) -> Optional[TrackLyrics]:
        """Fallback: fetch track lyrics from openapi.tidal.com/v2 (two-step).

        Step 1: resolve the lyrics ID from /tracks/{id}/relationships/lyrics.
        Step 2: fetch full content from /lyrics/{id} and map to TrackLyrics.
        """
        body = self._v2_get(f"tracks/{track_id}/relationships/lyrics")
        if not body:
            return None
        items = body.get("data", [])
        if not items:
            return None
        lyric_id = items[0].get("id") if isinstance(items, list) else items.get("id")
        if not lyric_id:
            return None
        body = self._v2_get(f"lyrics/{lyric_id}")
        if not body:
            return None
        attrs = body.get("data", {}).get("attributes", {})
        return TrackLyrics(
            lyrics=attrs.get("text"),
            subtitles=attrs.get("lrcText"),
        )

    def get_artist_videos(self, artist_id: ID) -> List[Video]:
        all_items = []
        offset = 0
        limit = Limits.ARTIST_VIDEOS_MAX

        while True:
            try:
                params = {
                    'limit': limit,
                    'offset': offset,
                    'countryCode': self.country_code
                }
                result = self._fetch_with_retry(
                    ArtistVideosItems, f'artists/{artist_id}/videos', params=params
                )
                if not result or not result.items:
                    break
                all_items.extend(result.items)
                if (offset + len(result.items)) >= result.total_number_of_items:
                    break
                offset += len(result.items)
                if len(result.items) < limit:
                    break
            except Exception:
                break

        if not all_items:
            log.debug(f"v1 returned no videos for artist {artist_id}, trying v2 fallback...")
            v2_videos = self._get_artist_videos_v2(artist_id)
            if v2_videos:
                log.info(f"v2 fallback found {len(v2_videos)} video(s) for artist {artist_id}")
            return v2_videos
        return all_items

    def get_artist_bio(self, artist_id: ID) -> Optional[ArtistBio]:
        try:
            return self._fetch_with_retry(
                ArtistBio, f"artists/{artist_id}/bio", {"countryCode": self.country_code}
            )
        except Exception:
            return None

    def get_artist_links(self, artist_id: ID) -> Optional[ArtistLinks]:
        try:
            return self._fetch_with_retry(
                ArtistLinks, f"artists/{artist_id}/links", {"countryCode": self.country_code}
            )
        except Exception:
            return None

    def get_artist_top_tracks(self, artist_id: ID) -> List[Track]:
        try:
            result = self._fetch_with_retry(
                ArtistTopTracks, f"artists/{artist_id}/toptracks",
                {"countryCode": self.country_code}
            )
            if result:
                return result.items
        except Exception:
            pass
        return []

    # ── Playlists ─────────────────────────────────────────────────────────────

    def get_playlist(self, playlist_uuid: str) -> Optional[Playlist]:
        """Get playlist metadata. Uses API v2."""
        try:
            return self._fetch_with_retry(
                Playlist, f"playlists/{playlist_uuid}",
                params={"countryCode": self.country_code},
                api_version="v2"
            )
        except Exception as e:
            log.error(f"Failed to fetch playlist {playlist_uuid}: {e}", exc_info=True)
            return None

    def get_playlist_items(self, playlist_uuid: str) -> List[Track]:
        all_items = []
        offset = 0
        limit = Limits.PLAYLIST_ITEMS_MAX

        while True:
            try:
                params = {'limit': limit, 'offset': offset, 'countryCode': self.country_code}
                result = self._fetch_with_retry(
                    PlaylistItems, f'playlists/{playlist_uuid}/items', params=params
                )
                if not result or not result.items:
                    break

                tracks = [
                    i.item for i in result.items
                    if i.type == 'track' and i.item is not None
                ]
                all_items.extend(tracks)

                if (offset + len(result.items)) >= result.total_number_of_items:
                    break
                offset += len(result.items)
                if len(result.items) < limit:
                    break

            except Exception as e:
                log.error(f"Error fetching playlist {playlist_uuid} offset {offset}: {e}")
                break
        return all_items

    # ── Tracks ────────────────────────────────────────────────────────────────

    def get_track(self, track_id: int) -> Optional[Track]:
        try:
            return self._fetch_with_retry(
                Track, f'tracks/{track_id}', {"countryCode": self.country_code}
            )
        except Exception:
            pass
        log.debug(f"v1 get_track failed for {track_id}, trying v2 fallback...")
        return self._get_track_v2(track_id)

    def get_track_lyrics(self, track_id: int) -> Optional[TrackLyrics]:
        try:
            return self._fetch_with_retry(
                TrackLyrics, f'tracks/{track_id}/lyrics', {"countryCode": self.country_code}
            )
        except Exception:
            pass
        log.debug(f"v1 get_track_lyrics failed for {track_id}, trying v2 fallback...")
        return self._get_track_lyrics_v2(track_id)

    def get_track_stream(self, track_id: int, quality: str = "LOSSLESS") -> Optional[TrackStream]:
        params = {
            "audioquality": quality,
            "playbackmode": "STREAM",
            "assetpresentation": "FULL",
            "countryCode": self.country_code
        }
        try:
            # max_retries=1: if the quality isn't available (500) fail fast so the
            # caller's quality-fallback loop can try the next quality immediately.
            return self._fetch_with_retry(
                TrackStream, f'tracks/{track_id}/playbackinfopostpaywall',
                max_retries=1, params=params
            )
        except Exception:
            return None

    def get_track_credits(self, track_id: ID) -> Optional[TrackCredits]:
        try:
            return self._fetch_with_retry(
                TrackCredits, f"tracks/{track_id}/contributors",
                {"countryCode": self.country_code}
            )
        except Exception:
            return None

    # ── Videos ────────────────────────────────────────────────────────────────

    def get_video(self, video_id: int) -> Optional[Video]:
        try:
            return self._fetch_with_retry(
                Video, f"videos/{video_id}", {"countryCode": self.country_code}
            )
        except Exception:
            return None

    def get_video_stream(self, video_id: int, quality: str = "HIGH") -> Optional[VideoStream]:
        params = {
            "videoquality": quality,
            "playbackmode": "STREAM",
            "assetpresentation": "FULL",
            "countryCode": self.country_code
        }
        try:
            # max_retries=1: fail fast so the caller's quality-fallback loop
            # can try the next quality immediately on server errors.
            return self._fetch_with_retry(
                VideoStream, f'videos/{video_id}/playbackinfopostpaywall',
                max_retries=1, params=params
            )
        except Exception:
            return None

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, search_type: str = 'ARTISTS', limit: int = 5) -> Optional[Search]:
        """
        Search TIDAL.
        search_type: 'ARTISTS', 'ALBUMS', 'TRACKS', 'ALL'
        """
        params = {
            'query': query,
            'limit': limit,
            'countryCode': self.country_code,
        }
        if search_type and search_type != 'ALL':
            params['types'] = search_type
        try:
            return self._fetch_with_retry(Search, 'search', params)
        except Exception:
            return None

    # ── Favorites ─────────────────────────────────────────────────────────────

    def get_user_favorite_artists(self, user_id: ID, limit: int = 50, offset: int = 0) -> Optional[Favorites]:
        params = {
            'limit': limit, 'offset': offset,
            'order': 'DATE', 'orderDirection': 'DESC',
            'countryCode': self.country_code
        }
        try:
            return self._fetch_with_retry(Favorites, f"users/{user_id}/favorites/artists", params)
        except Exception:
            return None