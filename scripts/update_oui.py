#!/usr/bin/env python3
"""Rebuild the bundled vendor database src/wifilab/data/oui.tsv.gz from the public IEEE registries
(MA-L oui.csv, MA-M mam.csv, MA-S oui36.csv). Stdlib only.

    python3 scripts/update_oui.py                 # download and rewrite the bundled file
    python3 scripts/update_oui.py --out FILE      # write somewhere else
    python3 scripts/update_oui.py --from-dir DIR  # use already downloaded oui.csv, mam.csv, oui36.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wifilab import oui  # noqa: E402

LOCAL_NAMES = {"MA-L": "oui.csv", "MA-M": "mam.csv", "MA-S": "oui36.csv"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "src" / "wifilab" / "data" / oui.DB_NAME)
    ap.add_argument("--from-dir", type=Path, help="read the registry CSVs from this folder instead of downloading")
    args = ap.parse_args()

    fetcher = oui.fetch
    if args.from_dir:
        by_url = {url: args.from_dir / LOCAL_NAMES[reg] for reg, url in oui.SOURCES.items()}

        def fetcher(url: str) -> str:
            return by_url[url].read_text(encoding="utf-8", errors="replace")

    try:
        res = oui.download(args.out, fetcher)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    size = args.out.stat().st_size
    print(f"wrote {args.out}: {res['entries']} prefixes (MA-L {res['MA-L']}, MA-M {res['MA-M']}, "
          f"MA-S {res['MA-S']}), {size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
