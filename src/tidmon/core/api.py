import time
import random
import logging
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
            return None

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
        return all_tracks

    # ── Artists ───────────────────────────────────────────────────────────────

    def get_artist(self, artist_id: ID) -> Optional[Artist]:
        try:
            return self._fetch_with_retry(
                Artist, f"artists/{artist_id}", {"countryCode": self.country_code}
            )
        except Exception:
            return None

    def get_artist_albums(self, artist_id: int) -> List[Album]:
        all_items = []
        seen_ids: set = set()

        for f in ["ALBUMS", "EPSANDSINGLES"]:
            offset = 0
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
        return all_items

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
            return None

    def get_track_lyrics(self, track_id: int) -> Optional[TrackLyrics]:
        try:
            return self._fetch_with_retry(
                TrackLyrics, f'tracks/{track_id}/lyrics', {"countryCode": self.country_code}
            )
        except Exception:
            return None

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