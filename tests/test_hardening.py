"""Security/reliability hardening ported from tiddl-elvigilante:
restricted unpickling, crash-safe atomic writes, and the Windows DNS resolver.
"""
import os
import pickle

import pytest

from tidmon.core.utils.safe_pickle import safe_loads, safe_load
from tidmon.core.utils.fsio import atomic_write_bytes, atomic_write_text


# ---------------------------------------------------------------- safe_pickle

def test_safe_pickle_loads_plain_data():
    data = {"modules": {"tidal": {"sessions": {"default": {"custom_data": {
        "sessions": {"TV": {"refresh_token": "abc", "user_id": 1, "country_code": "US"}}
    }}}}}}
    assert safe_loads(pickle.dumps(data)) == data


def test_safe_pickle_loads_containers_and_scalars():
    data = {"a": [1, 2, 3], "b": (True, 4.5), "c": {"x"}, "d": b"bytes"}
    assert safe_loads(pickle.dumps(data)) == data


class _Evil:
    def __reduce__(self):
        return (os.system, ("echo pwned",))


def test_safe_pickle_blocks_code_execution():
    payload = pickle.dumps(_Evil())
    with pytest.raises(pickle.UnpicklingError):
        safe_loads(payload)


def test_safe_pickle_blocks_arbitrary_class():
    import datetime
    # A datetime instance reconstructs via the non-builtin global datetime.datetime.
    payload = pickle.dumps(datetime.datetime(2020, 1, 1))
    with pytest.raises(pickle.UnpicklingError):
        safe_loads(payload)


def test_safe_load_from_file(tmp_path):
    p = tmp_path / "store.bin"
    p.write_bytes(pickle.dumps({"ok": True}))
    assert safe_load(p) == {"ok": True}


# ---------------------------------------------------------------- atomic writes

def test_atomic_write_bytes_roundtrip(tmp_path):
    p = tmp_path / "sub" / "auth.json"  # parent auto-created
    atomic_write_bytes(p, b'{"token":"x"}')
    assert p.read_bytes() == b'{"token":"x"}'


def test_atomic_write_overwrites_and_leaves_no_temp(tmp_path):
    p = tmp_path / "config.json"
    atomic_write_text(p, "v1")
    atomic_write_text(p, "v2")
    assert p.read_text() == "v2"
    # no leftover .config.json.*.tmp files
    leftovers = [x.name for x in tmp_path.iterdir() if x.name != "config.json"]
    assert leftovers == [], leftovers


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
def test_atomic_write_chmod_before_publish(tmp_path):
    p = tmp_path / "auth.json"
    atomic_write_bytes(p, b"secret", chmod_posix=0o600)
    assert (p.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------- DNS resolver

def test_make_resolver_none_on_non_windows(monkeypatch):
    import tidmon.core.downloader as dl
    monkeypatch.setattr(dl.sys, "platform", "linux")
    assert dl._make_resolver() is None


def test_make_resolver_threaded_on_windows(monkeypatch):
    import asyncio
    import tidmon.core.downloader as dl
    monkeypatch.setattr(dl.sys, "platform", "win32")

    # aiohttp.ThreadedResolver() binds to the running loop, so construct it
    # inside one (mirrors how _get_session is always called from async code).
    async def _make():
        return dl._make_resolver()

    r = asyncio.run(_make())
    assert type(r).__name__ == "ThreadedResolver"
