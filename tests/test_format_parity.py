"""Path-naming parity with tiddl-elvigilante.

tidmon and tiddl-elvigilante must render byte-identical file paths for the same
track so both tools can share one music library without ever creating duplicate
files. The GOLDENS below were produced by tiddl-elvigilante's own
`format_template` (see tools/parity_check) for each fixture; this test asserts
tidmon reproduces them exactly.

The fixtures deliberately cover every place the two implementations had drifted:
Unicode dashes, symbol transliteration, the artist "& others" cap, feat.
stripping, byte-truncation of CJK titles, multi-volume Disc folders, Atmos, and
tiddl's own bare default template. If this test fails after a change to
`format.py`/`strings.py`, the two tools will start producing duplicates.
"""
import pytest

from tidmon.core.utils.format import format_template
from tidmon.core.models.resources import Track, Album

TIDMON_DEFAULT = (
    "{artist_initials}/{album.artist}"
    "/({album.date:%Y}) {album.title} ({album.release})"
    "/{item.number}. {item.artists_with_features}"
    " - {item.title_version} {item.explicit:shortparens}"
)
TIDDL_DEFAULT = "{album.artist}/{album.title}/{item.title}"


def _artist(name, atype="MAIN", aid=1):
    return {"id": aid, "name": name, "type": atype}


# name -> (template, album_dict, track_dict)
FIXTURES = {
    "endash_in_title": (
        TIDMON_DEFAULT,
        {"id": 10, "title": "Album – Deluxe", "releaseDate": "2021-05-01",
         "type": "ALBUM", "artist": _artist("Artist X"), "artists": [_artist("Artist X")],
         "numberOfVolumes": 1},
        {"id": 100, "title": "Song – Live", "trackNumber": 1, "volumeNumber": 1,
         "explicit": True, "artists": [_artist("Artist X")], "artist": _artist("Artist X")},
    ),
    "star_symbol_and_slash": (
        TIDMON_DEFAULT,
        {"id": 11, "title": "★ Hits / Rarities", "releaseDate": "2019-08-15",
         "type": "COMPILATION", "artist": _artist("V/A"), "artists": [_artist("V/A")],
         "numberOfVolumes": 1},
        {"id": 101, "title": "Intro: The ★ Begins", "trackNumber": 2, "volumeNumber": 1,
         "explicit": False, "artists": [_artist("V/A")], "artist": _artist("V/A")},
    ),
    "many_artists_over_cap": (
        TIDMON_DEFAULT,
        {"id": 12, "title": "Posse Cut", "releaseDate": "2023-01-01", "type": "SINGLE",
         "artist": _artist("Lead"), "artists": [_artist("Lead")], "numberOfVolumes": 1},
        {"id": 102, "title": "All Stars", "trackNumber": 1, "volumeNumber": 1, "explicit": True,
         "artist": _artist("Lead"),
         "artists": [_artist("Lead"), _artist("B", aid=2), _artist("C", aid=3),
                     _artist("D", aid=4), _artist("E", "FEATURED", 5)]},
    ),
    "feat_in_title_known_artist": (
        TIDMON_DEFAULT,
        {"id": 13, "title": "Colab", "releaseDate": "2020-07-07", "type": "ALBUM",
         "artist": _artist("Main"), "artists": [_artist("Main")], "numberOfVolumes": 1},
        {"id": 103, "title": "Together (feat. Guest)", "trackNumber": 3, "volumeNumber": 1,
         "explicit": False, "artist": _artist("Main"),
         "artists": [_artist("Main"), _artist("Guest", "FEATURED", 2)]},
    ),
    "cjk_long_title": (
        TIDMON_DEFAULT,
        {"id": 14, "title": "アルバム" * 20, "releaseDate": "2022-03-03",
         "type": "ALBUM", "artist": _artist("アーティスト"),
         "artists": [_artist("アーティスト")], "numberOfVolumes": 1},
        {"id": 104, "title": "曲" * 60, "trackNumber": 4, "volumeNumber": 1,
         "explicit": False, "artist": _artist("アーティスト"),
         "artists": [_artist("アーティスト")]},
    ),
    "multivolume_disc_folder": (
        TIDMON_DEFAULT,
        {"id": 15, "title": "Double LP", "releaseDate": "2018-11-11", "type": "ALBUM",
         "artist": _artist("Band"), "artists": [_artist("Band")], "numberOfVolumes": 2},
        {"id": 105, "title": "Side C Opener", "trackNumber": 1, "volumeNumber": 2,
         "explicit": False, "artist": _artist("Band"), "artists": [_artist("Band")]},
    ),
    "atmos_track": (
        TIDMON_DEFAULT,
        {"id": 16, "title": "Spatial", "releaseDate": "2024-02-02", "type": "ALBUM",
         "artist": _artist("Producer"), "artists": [_artist("Producer")], "numberOfVolumes": 1,
         "mediaMetadata": {"tags": ["DOLBY_ATMOS"]}},
        {"id": 106, "title": "Immersive", "trackNumber": 1, "volumeNumber": 1, "explicit": False,
         "artist": _artist("Producer"), "artists": [_artist("Producer")],
         "mediaMetadata": {"tags": ["DOLBY_ATMOS"]}},
    ),
    "tiddl_default_template": (
        TIDDL_DEFAULT,
        {"id": 17, "title": "Simple", "releaseDate": "2015-06-06", "type": "ALBUM",
         "artist": _artist("Solo"), "artists": [_artist("Solo")], "numberOfVolumes": 1},
        {"id": 107, "title": "Track One", "trackNumber": 1, "volumeNumber": 1, "explicit": False,
         "artist": _artist("Solo"), "artists": [_artist("Solo")]},
    ),
}

# Produced by tiddl-elvigilante's format_template (with_asterisk_ext=False).
GOLDENS = {
    "endash_in_title": "A/Artist X/(2021) Album - Deluxe (ALBUM)/1. Artist X - Song - Live (explicit)",
    "star_symbol_and_slash": "V/V／A/(2019) ＊ Hits ／ Rarities (COMPILATION)/2. V／A - Intro： The ＊ Begins",
    "many_artists_over_cap": "L/Lead/(2023) Posse Cut (SINGLE)/1. B ／ C ／ D & others - All Stars (explicit)",
    "feat_in_title_known_artist": "M/Main/(2020) Colab (ALBUM)/3. Main ／ Guest - Together",
    "cjk_long_title": "#/アーティスト/(2022) アルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバムアルバム (ALBUM)/Item_104",
    "multivolume_disc_folder": "B/Band/(2018) Double LP (ALBUM)/Disc 2/1. Band - Side C Opener",
    "atmos_track": "P/Producer/(2024) Spatial (ALBUM)/1. Producer - Immersive",
    "tiddl_default_template": "Solo/Simple/Track One",
}


def _build_models(album_dict, track_dict):
    d = dict(track_dict)
    d["album"] = album_dict
    return Track.parse_obj(d), Album.parse_obj(album_dict)


@pytest.mark.parametrize("name", list(FIXTURES))
def test_format_parity_model(name):
    """Production path: tidmon pydantic models -> byte-identical to tiddl golden."""
    template, album_dict, track_dict = FIXTURES[name]
    track, album = _build_models(album_dict, track_dict)
    got = format_template(template, item=track, album=album, with_asterisk_ext=False)
    assert got == GOLDENS[name]


@pytest.mark.parametrize("name", list(FIXTURES))
def test_format_parity_dict(name):
    """Formatting-pipeline path: raw dict input -> byte-identical to tiddl golden.

    This exercises the sanitization/template/truncation pipeline independently of
    tidmon's model layer, mirroring how tiddl feeds data through safe_getattr.
    """
    template, album_dict, track_dict = FIXTURES[name]
    got = format_template(template, item=track_dict, album=album_dict, with_asterisk_ext=False)
    assert got == GOLDENS[name]


def test_cross_tool_live_if_available():
    """Anti-drift guard: when tiddl-elvigilante is importable (dev machine), assert
    tidmon reproduces its output live. Skipped in CI where tiddl isn't installed.
    """
    tiddl_format = pytest.importorskip("tiddl.core.utils.format")
    for name, (template, album_dict, track_dict) in FIXTURES.items():
        expected = tiddl_format.format_template(
            template, item=track_dict, album=album_dict, with_asterisk_ext=False
        )
        got = format_template(template, item=track_dict, album=album_dict, with_asterisk_ext=False)
        assert got == expected, f"cross-tool drift on {name}"
