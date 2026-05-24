import base64
import hashlib
import json
import logging
import secrets
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
from requests import Session, HTTPError, request as req
from typing import Callable, Optional

from .auth_exceptions import AuthClientError
from .auth_models import (
    AuthData,
    AuthDeviceResponse,
    AuthResponse,
    AuthResponseWithRefresh,
    TidalToken,
)

logger = logging.getLogger(__name__)

@dataclass
class TidalCredentials:
    """Credenciales de TIDAL con validación"""
    client_id: str
    client_secret: str

    def __post_init__(self):
        if not self.client_id or not self.client_secret:
            raise ValueError("client_id and client_secret are required")

    @classmethod
    def from_base64(cls, encoded: str) -> "TidalCredentials":
        """Carga credenciales desde string base64"""
        try:
            decoded = base64.b64decode(encoded).decode()
            client_id, client_secret = decoded.split(";")
            return cls(client_id, client_secret)
        except Exception as e:
            raise ValueError(f"Failed to decode credentials: {e}")

    def to_tuple(self) -> tuple[str, str]:
        """Retorna como tupla para compatibilidad"""
        return (self.client_id, self.client_secret)

class TokenStorage:
    """Almacenamiento seguro de tokens en disco"""
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, token: TidalToken):
        self.storage_path.write_text(json.dumps(token.to_dict(), indent=2))
        try:
            self.storage_path.chmod(0o600)
        except Exception:
            pass

    def load(self) -> Optional[TidalToken]:
        if not self.storage_path.exists():
            return None
        try:
            data = json.loads(self.storage_path.read_text())
            token = TidalToken.from_dict(data)
            return None if token.is_expired else token
        except Exception:
            return None

    def clear(self):
        if self.storage_path.exists():
            self.storage_path.unlink()

class AuthClient:
    """Cliente de autenticación mejorado"""
    def __init__(self, credentials: TidalCredentials, token_storage: TokenStorage):
        self.auth_url = "https://auth.tidal.com/v1/oauth2"
        self.credentials = credentials
        self.token_storage = token_storage
        self.session = Session()
        self._current_token: Optional[TidalToken] = self.token_storage.load()

    @property
    def current_token(self) -> Optional[TidalToken]:
        if self._current_token and self._current_token.expires_soon():
            try: self.refresh_current_token()
            except Exception: pass
        return self._current_token

    def refresh_token(self, refresh_token: str) -> TidalToken:
        res = self.session.post(
            f"{self.auth_url}/token",
            data={
                "client_id": self.credentials.client_id,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": "r_usr+w_usr+w_sub",
            },
            auth=self.credentials.to_tuple(),
        )
        self._handle_error(res)
        json_data = res.json()
        if "refresh_token" not in json_data:
            json_data["refresh_token"] = refresh_token
        token = TidalToken.from_api_response(json_data)
        self._current_token = token
        self.token_storage.save(token)
        return token

    def refresh_current_token(self) -> TidalToken:
        if not (self._current_token and self._current_token.refresh_token):
            raise ValueError("No refresh_token available")
        return self.refresh_token(self._current_token.refresh_token)

    def logout(self):
        if not self._current_token: return
        try:
            self.session.post(
                "https://api.tidal.com/v1/logout",
                headers={"authorization": f"Bearer {self._current_token.access_token}"},
            ).raise_for_status()
        except Exception as e:
            print(f"Warning: Logout request failed: {e}")
        self._current_token = None
        self.token_storage.clear()

    def device_flow(self) -> TidalToken:
        auth_data = self._start_device_auth()
        verification_uri = auth_data['verificationUri']
        user_code = auth_data['userCode']
        print(f"\n{'*'*50}")
        print("Para autorizar la aplicación, visita:")
        print(f"  {verification_uri}  y introduce el código: {user_code}")
        print(f"{'*'*50}\n")
        login_url = verification_uri if verification_uri.startswith("http") else f"https://{verification_uri}"
        try: webbrowser.open(login_url)
        except Exception: pass

        return self._poll_device_auth(auth_data['deviceCode'], auth_data.get('interval', 5), auth_data['expiresIn'])

    def _start_device_auth(self) -> dict:
        res = self.session.post(
            f"{self.auth_url}/device_authorization",
            data={"client_id": self.credentials.client_id, "scope": "r_usr+w_usr+w_sub"},
        )
        self._handle_error(res)
        return res.json()

    def _poll_device_auth(self, device_code: str, interval: int, timeout: int) -> TidalToken:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                res = self.session.post(
                    f"{self.auth_url}/token",
                    data={
                        "client_id": self.credentials.client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "scope": "r_usr+w_usr+w_sub",
                    },
                    auth=self.credentials.to_tuple(),
                )
                json_data = res.json()
                if res.status_code == 200:
                    token = TidalToken.from_api_response(json_data)
                    self._current_token = token
                    self.token_storage.save(token)
                    print("¡Autenticación exitosa!")
                    return token
                if res.status_code == 400 and json_data.get("error") == "authorization_pending":
                    time.sleep(interval)
                    continue
                self._handle_error(res)
            except (HTTPError, AuthClientError):
                raise
            except Exception as e:
                raise AuthClientError(status=500, error="unknown_error", error_description=str(e))
        raise AuthClientError(status=408, error="timeout", error_description="Device authorization timed out")

    def _handle_error(self, response):
        try:
            response.raise_for_status()
        except HTTPError as e:
            try: raise AuthClientError(**e.response.json())
            except Exception: raise AuthClientError(status=e.response.status_code, error="http_error", error_description=str(e)) from e


# ============================================================
# Single source of truth for AuthClient construction
# ============================================================

_DEFAULT_B64 = (
    "ZlgySnhkbW50WldLMGl4VDsxTm45QWZEQWp4cmdKRkpiS05XTGVBeUtHVkdtSU51"
    "WFBQTEhWWEF2eEFnPQ=="
)

TV_CREDENTIALS = TidalCredentials(
    client_id="4N3n6Q1x95LL5K7p",
    client_secret="oKOXfJW371cX6xaZ0PyhgGNBdNLlBZd4AKKYougMjik=",
)

MOBILE_ATMOS_CLIENT_ID = "km8T1xS355y7dd3H"
MOBILE_DEFAULT_CLIENT_ID = "6BDSRdpK9hqEBTgU"


def get_default_client_id() -> str:
    """Returns the default client_id for x-tidal-token fallback."""
    return TidalCredentials.from_base64(_DEFAULT_B64).client_id


def build_auth_client() -> "AuthClient":
    """
    Factory única para construir el AuthClient de tidmon.

    Credenciales OAuth y ruta de almacenamiento del token definidas
    en un único lugar — cualquier cambio futuro se hace solo aquí.
    """
    from tidmon.core.utils.startup import get_appdata_dir  # local: evita importación circular
    creds   = TidalCredentials.from_base64(_DEFAULT_B64)
    storage = TokenStorage(get_appdata_dir() / "tidal_token.json")
    return AuthClient(credentials=creds, token_storage=storage)


# ============================================================
# AuthData storage — mirrors tiddl's cli/utils/auth pattern
# ============================================================

def _get_auth_data_path() -> Path:
    from tidmon.core.utils.startup import get_appdata_dir  # local: evita importación circular
    return get_appdata_dir() / "auth.json"


def load_auth_data() -> AuthData:
    path = _get_auth_data_path()
    try:
        content = path.read_text()
    except FileNotFoundError:
        return AuthData()
    except Exception as e:
        logger.warning(f"Could not read auth file: {e}")
        return AuthData()
    try:
        return AuthData.parse_raw(content)
    except Exception as e:
        logger.warning(f"Could not parse auth file: {e}")
        return AuthData()


def save_auth_data(auth_data: AuthData) -> None:
    path = _get_auth_data_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(auth_data.json())
    except OSError as e:
        logger.warning(f"Could not write auth file at {path}: {e}")
        raise AuthClientError(
            error="storage_error",
            error_description=f"Failed to persist auth data to {path}: {e}",
        ) from e


# ============================================================
# AuthAPI — high-level wrapper that returns pydantic models
# (mirrors tiddl's core/auth/api.py pattern)
# ============================================================

_AUTH_URL = "https://auth.tidal.com/v1/oauth2"


def get_auth_api_for(client_id: str | None) -> "AuthAPI":
    """Returns AuthAPI with correct credentials based on stored client_id."""
    if client_id and client_id == TV_CREDENTIALS.client_id:
        return AuthAPI(credentials=TV_CREDENTIALS)
    return AuthAPI()


class AuthAPI:
    """High-level auth wrapper that returns typed pydantic models."""

    def __init__(self, credentials: TidalCredentials | None = None) -> None:
        self._credentials = credentials or TV_CREDENTIALS
        self._session = Session()

    def get_device_auth(self) -> AuthDeviceResponse:
        res = self._session.post(
            f"{_AUTH_URL}/device_authorization",
            data={"client_id": self._credentials.client_id, "scope": "r_usr+w_usr+w_sub"},
        )
        res.raise_for_status()
        return AuthDeviceResponse.parse_obj(res.json())

    def get_auth(self, device_code: str) -> AuthResponseWithRefresh:
        """Single poll attempt — raises AuthClientError('authorization_pending') if not yet approved."""
        res = self._session.post(
            f"{_AUTH_URL}/token",
            data={
                "client_id": self._credentials.client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "scope": "r_usr+w_usr+w_sub",
            },
            auth=self._credentials.to_tuple(),
        )
        if res.status_code != 200:
            try:
                json_data = res.json()
                raise AuthClientError(**json_data)
            except (ValueError, TypeError):
                raise AuthClientError(
                    status=res.status_code,
                    error="http_error",
                    error_description=res.text[:200],
                )
        return AuthResponseWithRefresh.parse_obj(res.json())

    def refresh_token(self, refresh_token: str) -> AuthResponse:
        res = self._session.post(
            f"{_AUTH_URL}/token",
            data={
                "client_id": self._credentials.client_id,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": "r_usr+w_usr+w_sub",
            },
            auth=self._credentials.to_tuple(),
        )
        res.raise_for_status()
        return AuthResponse.parse_obj(res.json())

    def logout_token(self, access_token: str) -> None:
        res = self._session.post(
            "https://api.tidal.com/v1/logout",
            headers={"authorization": f"Bearer {access_token}"},
        )
        if not res.ok:
            body = res.text[:200]
            logger.warning("TIDAL logout failed: status=%s, body=%s", res.status_code, body)


class MobileAuthClient:
    """TIDAL Mobile OAuth2 with PKCE (username/password flow, ported from OrpheusDL)."""

    _LOGIN_BASE = "https://login.tidal.com/api/"
    _USER_AGENT = (
        "Mozilla/5.0 (Linux; Android 13; Pixel 8 Build/TQ2A.230505.002; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/119.0.6045.163 "
        "Mobile Safari/537.36"
    )
    _REDIRECT_URI = "https://tidal.com/android/login/auth"

    def __init__(self, client_id: str = MOBILE_DEFAULT_CLIENT_ID):
        self.client_id = client_id
        self._code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=")
        self._code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(self._code_verifier).digest()
        ).rstrip(b"=")
        self._client_unique_key = secrets.token_hex(8)

    def auth(self, username: str, password: str) -> dict:
        """Login with username/password. Returns raw token dict with user_id and country_code."""
        s = Session()
        params = {
            "response_type": "code",
            "redirect_uri": self._REDIRECT_URI,
            "lang": "en_US",
            "appMode": "android",
            "client_id": self.client_id,
            "client_unique_key": self._client_unique_key,
            "code_challenge": self._code_challenge,
            "code_challenge_method": "S256",
            "restrict_signup": "true",
        }
        common = {
            "User-Agent": self._USER_AGENT,
            "Accept-Language": "en-US",
            "X-Requested-With": "com.aspiro.tidal",
        }

        r = s.get("https://login.tidal.com/authorize", params=params, headers=common)
        if r.status_code == 400:
            raise AuthClientError(status=400, error="auth_failed", error_description="Invalid client_id")
        if r.status_code == 403:
            raise AuthClientError(status=403, error="bot_protection", error_description="Bot protection triggered, try again later")

        dd = s.post("https://dd.tidal.com/js/", data={
            "jsData": f'{{"opts":"endpoint,ajaxListenerPath","ua":"{self._USER_AGENT}"}}',
            "ddk": "1F633CDD8EF22541BD6D9B1B8EF13A",
            "Referer": quote(r.url),
            "responsePage": "origin",
            "ddv": "4.17.0",
        }, headers={"User-Agent": self._USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"})
        if dd.status_code != 200 or not dd.json().get("cookie"):
            raise AuthClientError(status=403, error="bot_protection", error_description="Could not obtain DataDome cookie")
        raw_cookie = dd.json()["cookie"].split(";")[0]
        s.cookies[raw_cookie.split("=")[0]] = raw_cookie.split("=")[1]

        csrf = s.cookies.get("_csrf-token", "")
        json_h = {**common, "X-CSRF-Token": csrf, "Accept": "application/json, text/plain, */*", "Content-Type": "application/json"}

        r = s.post(self._LOGIN_BASE + "email", params=params, json={"email": username}, headers=json_h)
        if r.status_code != 200:
            raise AuthClientError(status=r.status_code, error="email_check_failed", error_description=r.text)
        if not r.json().get("isValidEmail"):
            raise AuthClientError(status=400, error="invalid_email", error_description="Invalid email address")
        if r.json().get("newUser"):
            raise AuthClientError(status=400, error="user_not_found", error_description="User does not exist")

        r = s.post(self._LOGIN_BASE + "email/user/existing", params=params,
                   json={"email": username, "password": password}, headers=json_h)
        if r.status_code != 200:
            raise AuthClientError(status=r.status_code, error="login_failed", error_description=r.text)

        r = s.get("https://login.tidal.com/success", allow_redirects=False, headers=common)
        if r.status_code == 401:
            raise AuthClientError(status=401, error="wrong_password", error_description="Incorrect password")
        if r.status_code != 302:
            raise AuthClientError(status=r.status_code, error="auth_failed", error_description="Expected redirect after login")
        oauth_code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

        r = req("POST", "https://auth.tidal.com/v1/oauth2/token", data={
            "code": oauth_code,
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "redirect_uri": self._REDIRECT_URI,
            "scope": "r_usr w_usr w_sub",
            "code_verifier": self._code_verifier,
            "client_unique_key": self._client_unique_key,
        }, headers={"User-Agent": self._USER_AGENT})
        if r.status_code != 200:
            raise AuthClientError(status=r.status_code, error="token_exchange_failed", error_description=r.text)
        data = r.json()

        r = req("GET", "https://api.tidal.com/v1/sessions", headers={
            "Authorization": f"Bearer {data['access_token']}",
            "X-Tidal-Token": self.client_id,
            "User-Agent": "TIDAL_ANDROID/1039 okhttp/3.14.9",
        })
        if r.status_code == 200:
            sess = r.json()
            data["user_id"] = sess["userId"]
            data["country_code"] = sess["countryCode"]

        return data

    def refresh(self, refresh_token: str) -> dict:
        """Refresh a mobile token (no client secret needed)."""
        r = req("POST", "https://auth.tidal.com/v1/oauth2/token", data={
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "grant_type": "refresh_token",
        })
        r.raise_for_status()
        return r.json()