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
    ):
        self.on_token_expiry = on_token_expiry
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

    def fetch(
        self,
        model: Type[T],
        endpoint: str,
        params: dict[str, Any] = {},
        api_version: Literal["v1", "v2"] = "v1",
        _refreshed: bool = False  # Internal flag to prevent retry loops
    ) -> T:
        base_url = API_V1_URL if api_version == "v1" else API_V2_URL
        url = f"{base_url}/{endpoint}"
        
        try:
            # Bypass cache for stream/token-sensitive endpoints
            _no_cache = any(p in url for p in _NO_CACHE_PATTERNS)

            # Adaptive delay: grows on 429, shrinks on success
            if self._rate_limit_delay > 0:
                time.sleep(self._rate_limit_delay)

            # Rate limit: thread-safe fixed interval + jitter
            with self._rate_lock:
                elapsed = time.monotonic() - self._last_request_time
                wait = self._request_interval - elapsed + random.uniform(0, 0.3)
                if wait > 0:
                    time.sleep(wait)
                self._last_request_time = time.monotonic()

            response = self.session.get(
                url, params=params,
                expire_after=0 if _no_cache else None,
            )

            # Cache hits don't consume API quota — release the slot
            if getattr(response, 'from_cache', False):
                with self._rate_lock:
                    self._last_request_time = time.monotonic() - self._request_interval
            
            # Reactive token refresh on 401
            if response.status_code == 401 and not _refreshed:
                # Guard against content-specific 401s (geo-block, etc.)
                try:
                    sub_status = response.json().get("subStatus")
                    if sub_status == 4005:
                        log.debug("Asset not ready (401/4005) - skipping token refresh")
                        response.raise_for_status() # Re-raise the original error
                except (JSONDecodeError, AttributeError):
                    pass # Not a JSON response or no subStatus, proceed with refresh

                log.warning("Token may have expired (401). Attempting to refresh...")
                if self.on_token_expiry:
                    new_token = self.on_token_expiry(force=True)
                    if new_token:
                        log.info("Token refreshed successfully. Retrying request...")
                        self.token = new_token  # Use setter to update headers
                        return self.fetch(model, endpoint, params, api_version, _refreshed=True)
                log.error("Token refresh failed or callback not provided. Aborting.")

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