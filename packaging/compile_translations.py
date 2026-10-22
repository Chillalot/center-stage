"""Compile gettext catalogs without requiring the system msgfmt binary."""

from __future__ import annotations

import sys
from pathlib import Path

import polib


def compile_catalog(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    catalog = polib.pofile(str(source))
    catalog.save_as_mofile(str(destination))


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    root = Path(__file__).resolve().parent.parent
    source = Path(arguments[0]) if len(arguments) > 0 else root / "po" / "vi.po"
    destination = (
        Path(arguments[1])
        if len(arguments) > 1
        else root / "build" / "locale" / "vi" / "LC_MESSAGES" / "center-stage.mo"
    )
    compile_catalog(source, destination)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
