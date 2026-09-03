"""Path-naming parity with tiddl-elvigilante.

tidmon and tiddl-elvigilante must render byte-identical file paths for the same
track so both tools can share one music library without ever creating duplicate
files. The GOLDENS below were produced by tiddl-elvigilante's own
`format_template` (regenerate with tools/regen_format_goldens.py) for each
fixture; this test asserts tidmon reproduces them exactly.

The fixtures deliberately cover every place the two implementations had drifted:
Unicode dashes, symbol transliteration, the artist "& others" cap, feat.
stripping, byte-truncation of CJK titles, multi-volume Disc folders, Atmos, and
tiddl's own bare default template. If this test fails after a change to
`format.py`/`strings.py`, the two tools will start producing duplicates.
"""
import pytest

from tidmon.core.utils.format import format_template
from tidmon.core.models.resources import Track, Album

from format_fixtures import FIXTURES, GOLDENS  # noqa: E402


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
