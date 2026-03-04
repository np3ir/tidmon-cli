import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional
from pydantic import BaseModel


@dataclass
class TidalToken:
    """Token de TIDAL con manejo de expiración"""
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int = 604800  # 7 días por defecto
    created_at: float = field(default_factory=time.time)
    user_data: Optional[dict] = None

    @property
    def expires_at(self) -> float:
        """Timestamp de cuando expira el token"""
        return self.created_at + self.expires_in

    @property
    def is_expired(self) -> bool:
        """Verifica si el token ya expiró"""
        return time.time() >= self.expires_at

    def expires_soon(self, threshold_seconds: int = 3600) -> bool:
        """
        Verifica si el token expira pronto
        Args:
            threshold_seconds: Segundos antes de expiración (default: 1 hora)
        """
        return (self.expires_at - time.time()) < threshold_seconds

    @property
    def time_remaining(self) -> timedelta:
        """Tiempo restante antes de expiración"""
        seconds = max(0, self.expires_at - time.time())
        return timedelta(seconds=seconds)

    def to_dict(self) -> dict:
        """Serializa a diccionario para guardar"""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_in": self.expires_in,
            "created_at": self.created_at,
            "user_data": self.user_data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TidalToken":
        """Carga desde diccionario"""
        return cls(**data)

    @classmethod
    def from_api_response(cls, response: dict) -> "TidalToken":
        """Crea token desde respuesta de la API de TIDAL"""
        return cls(
            access_token=response["access_token"],
            refresh_token=response.get("refresh_token"),
            expires_in=response.get("expires_in", 604800),
            user_data=response.get("user"),
        )


class AuthResponse(BaseModel):
    class User(BaseModel):
        userId: int
        email: str
        countryCode: str
        fullName: Optional[str] = None
        firstName: Optional[str] = None
        lastName: Optional[str] = None
        nickname: Optional[str] = None
        username: str
        address: Optional[str] = None
        city: Optional[str] = None
        postalcode: Optional[str] = None
        usState: Optional[str] = None
        phoneNumber: Optional[str] = None
        birthday: Optional[int] = None
        channelId: int
        parentId: int
        acceptedEULA: bool
        created: int
        updated: int
        facebookUid: int
        appleUid: Optional[str] = None
        googleUid: Optional[str] = None
        accountLinkCreated: bool
        emailVerified: bool
        newUser: bool

    user: User
    scope: str
    clientName: str
    token_type: str
    access_token: str
    expires_in: int
    user_id: int
    refresh_token: Optional[str] = None


class AuthResponseWithRefresh(AuthResponse):
    refresh_token: Optional[str] = None


class AuthDeviceResponse(BaseModel):
    deviceCode: str
    userCode: str
    verificationUri: str
    verificationUriComplete: str
    expiresIn: int
    interval: int


class AuthData(BaseModel):
    token: str | None = None
    refresh_token: str | None = None
    expires_at: int = 0
    user_id: str | None = None
    country_code: str | None = None
