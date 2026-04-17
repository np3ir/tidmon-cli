from pydantic import BaseModel, Field, validator
from typing import List, Optional, Union, Any

from .resources import Album, Artist, Track, Video, Playlist, Contributor


class Item(BaseModel):
    item: Union[Track, Video, Album, Artist, Playlist, Any]
    type: str


class AlbumItems(BaseModel):
    limit: int
    offset: int
    total_number_of_items: int = Field(..., alias='totalNumberOfItems')
    items: List[Track] = []

    @validator('items', pre=True)
    @classmethod
    def unwrap_items(cls, v):
        if isinstance(v, list):
            result = []
            for i in v:
                if isinstance(i, dict):
                    result.append(i.get('item', i))
                else:
                    result.append(i)
            return result
        return v

    class Config:
        allow_population_by_field_name = True


class ArtistAlbumsItems(BaseModel):
    limit: int
    offset: int
    total_number_of_items: int = Field(..., alias='totalNumberOfItems')
    items: List[Album] = []

    class Config:
        allow_population_by_field_name = True


class ArtistSearchItems(BaseModel):
    limit: int
    offset: int
    total_number_of_items: int = Field(..., alias='totalNumberOfItems')
    items: List[Artist] = []

    model_config = {"populate_by_name": True}


class PlaylistItems(BaseModel):
    limit: int
    offset: int
    total_number_of_items: int = Field(..., alias='totalNumberOfItems')
    items: List[Item] = []

    model_config = {"populate_by_name": True}


class Search(BaseModel):
    artists: Optional[ArtistSearchItems] = None
    albums: Optional[ArtistAlbumsItems] = None
    tracks: Optional[AlbumItems] = None
    videos: Optional[Any] = None
    playlists: Optional[Any] = None


class SessionResponse(BaseModel):
    session_id: str = Field(..., alias='sessionId')
    user_id: int = Field(..., alias='userId')
    country_code: str = Field(..., alias='countryCode')

    model_config = {"populate_by_name": True}


class TrackLyrics(BaseModel):
    lyrics: Optional[str] = None
    subtitles: Optional[str] = None


class AlbumItemsCredits(BaseModel):
    limit: int
    offset: int
    total_number_of_items: int = Field(..., alias='totalNumberOfItems')
    items: List[Contributor] = []

    model_config = {"populate_by_name": True}


class ArtistVideosItems(BaseModel):
    limit: int
    offset: int
    total_number_of_items: int = Field(..., alias='totalNumberOfItems')
    items: List[Video] = []

    model_config = {"populate_by_name": True}

    @validator('items', pre=True)
    @classmethod
    def skip_invalid_videos(cls, v):
        if not isinstance(v, list):
            return v
        result = []
        for item in v:
            try:
                Video(**item) if isinstance(item, dict) else item
                result.append(item)
            except Exception:
                pass
        return result


class Favorites(BaseModel):
    limit: int
    offset: int
    total_number_of_items: int = Field(..., alias='totalNumberOfItems')
    items: List[Any] = []

    model_config = {"populate_by_name": True}


class MixItems(BaseModel):
    limit: int
    offset: int
    total_number_of_items: int = Field(..., alias='totalNumberOfItems')
    items: List[Any] = []

    model_config = {"populate_by_name": True}
