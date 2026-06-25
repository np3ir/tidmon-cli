import json
import random
import threading
import time
from logging import getLogger
from pathlib import Path
from typing import Any, Type, TypeVar, Callable, Optional, Literal
from datetime import timedelta

from pydantic import BaseModel
from time import sleep

# CRITICAL FIX: Import HTTPError
from requests.exceptions import JSONDecodeError, HTTPError
from requests_cache import (
    CachedSession,
    StrOrPath,
    NEVER_EXPIRE,
)
from datetime import timedelta as _td

# Endpoints that must never be served from cache (stream URLs, token-sensitive)
_NO_CACHE_PATTERNS = [
    "playbackinfopostpaywall",
]

# Endpoints that require auth — all others can fall back to x-tidal-token
_AUTH_REQUIRED = ("playbackinfo", "playback", "logout", "token", "events")

from tidmon.core.exceptions import ApiError
from tidmon.core.utils.startup import get_appdata_dir

T = TypeVar("T", bound=BaseModel)

API_URL = "https://api.tidal.com/v1"
API_V1_URL = "https://api.tidal.com/v1"
API_V2_URL = "https://api.tidal.com/v2"  # For Feed and Activity API
MAX_RETRIES = 5
RETRY_DELAY = 2

log = getLogger(__name__)


class TidalClientImproved:
    """HTTP client for the TIDAL API."""

    def __init__(
        self,
        token: str,
        on_token_expiry: Optional[Callable[..., Optional[str]]] = None,
        requests_per_minute: int = 50,
        anonymous: bool = False,
    ):
        self.on_token_expiry = on_token_expiry
        # Anonymous mode: catalogue reads go out strictly via x-tidal-token; the
        # user's Bearer token is NEVER attached and never refreshed. Used by the
        # detection path (refresh/monitor) so polling can't disturb or flag the
        # personal account. Downloads still need a real (non-anonymous) client.
        self.anonymous = anonymous

        # Store client_id for x-tidal-token fallback on public endpoints
        from tidmon.core.auth_client import get_default_client_id
        self._client_id = get_default_client_id()

        # Rate limiting: thread-safe fixed interval + jitter + adaptive delay
        safe_rpm = requests_per_minute if requests_per_minute > 0 else 50
        self._last_request_time: float = 0.0
        self._request_interval: float = 60.0 / safe_rpm
        self._rate_lock = threading.Lock()
        self._rate_limit_delay: float = 0.0  # Adaptive: grows on 429, shrinks on success

        self.session = CachedSession(
            cache_name=get_appdata_dir() / "tidal_api_cache.sqlite",
            backend='sqlite',
            expire_after=timedelta(hours=1),
            allowable_codes=[200],
        )
        self.session.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Origin": "https://listen.tidal.com",
            "Referer": "https://listen.tidal.com/",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }

    @property
    def token(self) -> str:
        auth_header = self.session.headers.get("Authorization", "Bearer ")
        return auth_header.split(" ")[1]

    @token.setter
    def token(self, new_token: str):
        self.session.headers["Authorization"] = f"Bearer {new_token}"

    def _public_headers(self) -> dict:
        """Headers for public endpoints — x-tidal-token instead of Bearer."""
        h = {k: v for k, v in self.session.headers.items()
             if k.lower() != "authorization"}
        h["x-tidal-token"] = self._client_id
        return h

    def _needs_auth(self, endpoint: str) -> bool:
        """Returns True only for endpoints that require a Bearer token."""
        return any(p in endpoint for p in _AUTH_REQUIRED)

    def fetch(
        self,
        model: Type[T],
        endpoint: str,
        params: dict[str, Any] = {},
        api_version: Literal["v1", "v2"] = "v1",
        _refreshed: bool = False
    ) -> T:
        base_url = API_V1_URL if api_version == "v1" else API_V2_URL
        url = f"{base_url}/{endpoint}"

        # Anonymous client: refuse endpoints that require a user Bearer token
        # (playback, logout, token, events). Detection/catalogue reads never hit
        # these, so this only guards against accidental account use.
        if self.anonymous and self._needs_auth(endpoint):
            raise ApiError(
                userMessage=f"Endpoint '{endpoint}' requires login; client is anonymous.",
                status=401,
            )

        try:
            _no_cache = any(p in url for p in _NO_CACHE_PATTERNS)

            # Adaptive delay
            if self._rate_limit_delay > 0:
                time.sleep(self._rate_limit_delay)

            # Rate limit
            with self._rate_lock:
                elapsed = time.monotonic() - self._last_request_time
                # Randomize the target interval every request so the cadence is NOT a
                # constant metronome — a steady fixed rate is one of the clearest bot
                # signals for anti-bot systems (DataDome). Mean is ~1.0x the configured
                # interval (the irregularity, not an inflated mean, is what defeats
                # bot-detection), with an occasional longer "human" pause.
                target = self._request_interval * random.uniform(0.5, 1.5)
                if random.random() < 0.07:
                    target += random.uniform(4.0, 10.0)
                wait = target - elapsed
                if wait > 0:
                    time.sleep(wait)
                self._last_request_time = time.monotonic()

            # Public endpoints use x-tidal-token directly — no Bearer needed
            if not self._needs_auth(endpoint):
                import requests as _req
                response = _req.get(url, params=params,
                                    headers=self._public_headers(), timeout=15)
                if response.status_code == 200:
                    self._rate_limit_delay = max(0.0, self._rate_limit_delay - 0.1)
                    return model(**response.json())
                # Adaptive backoff on rate-limit even for the public path
                if response.status_code == 429:
                    self._rate_limit_delay = min(5.0, self._rate_limit_delay + 1.0)
                # Anonymous mode: never fall back to the user's Bearer token. Surface
                # the public-endpoint status so the caller's retry/skip logic handles
                # it (429 → backoff+retry, 4xx → skip) without ever touching the account.
                if self.anonymous:
                    raise ApiError(
                        userMessage=f"HTTP {response.status_code} for {url} (anonymous public call)",
                        status=response.status_code,
                    )
                # If public call fails, fall through to Bearer attempt below
                log.debug(f"x-tidal-token failed ({response.status_code}) for {endpoint} — trying Bearer")

            # Auth-required endpoints (or public fallback) use Bearer
            response = self.session.get(
                url, params=params,
                expire_after=0 if _no_cache else None,
            )

            if getattr(response, 'from_cache', False):
                with self._rate_lock:
                    self._last_request_time = time.monotonic() - self._request_interval

            # Token refresh on 401
            if response.status_code == 401 and not _refreshed:
                try:
                    sub_status = response.json().get("subStatus")
                    if sub_status == 4005:
                        log.debug("Asset not ready (401/4005)")
                        response.raise_for_status()
                except (JSONDecodeError, AttributeError):
                    pass

                log.warning("Token expired (401). Attempting refresh...")
                if self.on_token_expiry:
                    new_token = self.on_token_expiry(force=True)
                    if new_token:
                        self.token = new_token
                        return self.fetch(model, endpoint, params, api_version, _refreshed=True)

                log.error("Token refresh failed. Aborting.")

            if response.status_code == 429:
                self._rate_limit_delay = min(5.0, self._rate_limit_delay + 1.0)
            elif response.status_code == 200:
                self._rate_limit_delay = max(0.0, self._rate_limit_delay - 0.1)

            response.raise_for_status()
            return model(**response.json())
        except HTTPError as http_err:
            raise ApiError(
                userMessage=f"HTTP Error {http_err.response.status_code} for {url}",
                status=http_err.response.status_code,
            ) from http_err
        except (JSONDecodeError, Exception) as err:
            log.error(f"Error fetching {url}: {err}")
            raise ApiError(userMessage=f"Failed to fetch or decode from {url}", status=500) from err