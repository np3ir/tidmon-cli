import logging
from time import time
from typing import Optional

from .api import TidalAPI
from .client import TidalClientImproved
from .auth_client import AuthAPI, load_auth_data, save_auth_data
from .config import Config

logger = logging.getLogger(__name__)


class TidalSession:
    """Manages the TIDAL API session using tiddl's AuthAPI + AuthData pattern."""

    def __init__(self):
        self.api: Optional[TidalAPI] = None
        self._auth_api = AuthAPI()

    def get_api(self) -> TidalAPI:
        """
        Returns a valid TidalAPI instance.

        Loads auth data from disk (AuthData) and builds the API client.
        Token refresh is handled reactively via on_token_expiry callback.
        """
        if self.api is not None:
            return self.api

        auth_data = load_auth_data()

        if not auth_data.token:
            raise ConnectionError("Not authenticated. Please run 'tidmon auth' first.")

        if not auth_data.user_id:
            raise ConnectionError("User ID is missing. Please run 'tidmon auth' first.")

        if not auth_data.country_code:
            raise ConnectionError("Country code is missing. Please run 'tidmon auth' first.")

        logger.debug("Creating new TidalAPI instance.")

        def _on_token_expiry(force: bool = False) -> Optional[str]:
            """Token refresh callback for TidalClientImproved."""
            try:
                latest = load_auth_data()
                if not latest.refresh_token:
                    # Web token has no refresh_token — return None gracefully
                    return None

                # Skip network refresh if token is still valid and not forced
                if not force and latest.expires_at and latest.expires_at > int(time()) + 60:
                    return latest.token

                logger.info(f"Refreshing token (force={force}).")
                auth_response = self._auth_api.refresh_token(latest.refresh_token)

                latest.token = auth_response.access_token
                latest.expires_at = auth_response.expires_in + int(time())
                if auth_response.refresh_token:
                    latest.refresh_token = auth_response.refresh_token

                save_auth_data(latest)
                return auth_response.access_token

            except Exception as e:
                logger.error(f"Token refresh failed: {e}", exc_info=True)
                return None

        client = TidalClientImproved(
            token=auth_data.token,
            on_token_expiry=_on_token_expiry,
            requests_per_minute=Config().get("requests_per_minute", 20),
        )

        self.api = TidalAPI(
            client=client,
            user_id=auth_data.user_id,
            country_code=auth_data.country_code,
        )
        return self.api


def get_session() -> TidalSession:
    """Global factory to get the TidalSession."""
    return TidalSession()
