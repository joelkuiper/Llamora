"""Extract the compressed demo database to data/demo_data.sqlite3."""

import lzma
import shutil
from pathlib import Path

SRC = Path(__file__).parent.parent / "data" / "demo_data.sqlite3.lzma"
DST = SRC.with_suffix("")  # data/demo_data.sqlite3


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Source not found: {SRC}")
    if DST.exists():
        print(f"Already exists: {DST}")
        return
    print(f"Extracting {SRC.name} → {DST.name} …")
    with lzma.open(SRC, "rb") as src, open(DST, "wb") as dst:
        shutil.copyfileobj(src, dst)
    print(f"Done: {DST}")


if __name__ == "__main__":
    main()
