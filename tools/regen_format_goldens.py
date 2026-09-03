"""Regenerate the path-parity goldens in tests/test_format_parity.py.

The goldens are the output of tiddl-elvigilante's OWN `format_template` for each
fixture, so tidmon must reproduce them exactly (see tests/test_format_parity.py).
This script is a DEV tool: it requires `tiddl` (tiddl-elvigilante) to be
importable. CI does not run it — it uses the baked goldens.

Usage (from the tidmon-cli repo root, with a Python that can import BOTH tools):
    python tools/regen_format_goldens.py

It reuses the FIXTURES defined in tests/test_format_parity.py as the single
source of truth, runs them through tiddl's engine, and prints an updated GOLDENS
dict to paste back into the test. It also cross-checks tidmon's current output
and reports any drift.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

try:
    import tiddl.core.utils.format as tiddl_format  # noqa: E402
except ImportError:
    sys.exit("tiddl-elvigilante is not importable; install it to regenerate goldens.")

import tidmon.core.utils.format as tidmon_format  # noqa: E402
from format_fixtures import FIXTURES  # noqa: E402


def main() -> int:
    goldens = {}
    drift = []
    for name, (template, album, track) in FIXTURES.items():
        golden = tiddl_format.format_template(
            template, item=track, album=album, with_asterisk_ext=False
        )
        goldens[name] = golden
        got = tidmon_format.format_template(
            template, item=track, album=album, with_asterisk_ext=False
        )
        if got != golden:
            drift.append((name, golden, got))

    print("GOLDENS = " + json.dumps(goldens, ensure_ascii=False, indent=4))
    if drift:
        print("\n*** tidmon DRIFT vs tiddl ***")
        for name, golden, got in drift:
            print(f"  {name}\n    tiddl : {golden!r}\n    tidmon: {got!r}")
        return 1
    print("\nAll fixtures: tidmon == tiddl.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
