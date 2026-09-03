"""Unit tests for the filesystem-safe string primitives ported from
tiddl-elvigilante. These lock the exact behaviours that had drifted between the
two tools and would otherwise re-introduce duplicate files."""
from tidmon.core.utils.strings import (
    sanitize_filename, dedup_artists, truncate_str_bytes,
    DASH_TO_HYPHEN, RESERVED_BYTE_COUNT, MAX_COMPONENT_LEN,
    normalize_artist_name, get_alpha_bucket,
)


def test_reserved_constants_match_tiddl():
    # These exact values keep tidmon and tiddl truncating names identically.
    assert RESERVED_BYTE_COUNT == 24
    assert MAX_COMPONENT_LEN == 255


def test_unicode_dashes_fold_to_ascii_hyphen():
    # en-dash, em-dash, minus, horizontal bar all -> '-'
    for dash in ["–", "—", "−", "―", "‐"]:
        out = sanitize_filename(f"A {dash} B")
        assert out == "A - B", (dash, out)


def test_star_symbol_transliterated():
    assert sanitize_filename("★ Hits") == "＊ Hits"  # forbidden '*' -> full-width after translit


def test_forbidden_chars_go_fullwidth():
    out = sanitize_filename('a/b:c?d"e')
    assert "/" not in out and ":" not in out and "?" not in out and '"' not in out
    assert "／" in out and "：" in out and "？" in out


def test_dedup_artists_accent_and_case_insensitive():
    assert dedup_artists(["ROSALÍA", "Rosalia", "Bad Bunny"]) == ["ROSALÍA", "Bad Bunny"]


def test_dedup_artists_excludes_main_from_featured():
    main = ["Drake"]
    assert dedup_artists(["Drake", "Future"], exclude=main) == ["Future"]


def test_normalize_artist_name_key():
    assert normalize_artist_name("  ROSALÍA ") == normalize_artist_name("rosalia")


def test_truncate_str_bytes_is_byte_aware():
    # 3-byte CJK char; 7 bytes cap -> 2 full chars (6 bytes), never a partial byte
    s = "曲" * 5
    out = truncate_str_bytes(s, 7)
    assert out == "曲曲"
    assert len(out.encode("utf-8")) <= 7


def test_get_alpha_bucket_non_latin_is_hash():
    assert get_alpha_bucket("アーティスト") == "#"
    assert get_alpha_bucket("Éowyn") == "E"
    assert get_alpha_bucket("") == "#"


def test_junk_symbol_name_falls_back():
    # almost entirely symbols -> deterministic fallback, not an empty component
    out = sanitize_filename("!!!@@@###", item_id=999)
    assert out and out != ""
