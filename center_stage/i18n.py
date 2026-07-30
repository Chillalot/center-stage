"""Small gettext wrapper with English as the explicit default."""

from __future__ import annotations

import gettext
from pathlib import Path
from typing import Callable, Iterable

DOMAIN = "center-stage"


def locale_directories() -> tuple[Path, ...]:
    return (
        Path(__file__).resolve().parent.parent / "locale",
        Path("/usr/share/locale"),
    )


def translator(
    language: str, directories: Iterable[Path] | None = None
) -> Callable[[str], str]:
    if language != "vi":
        return gettext.NullTranslations().gettext
    for directory in directories or locale_directories():
        try:
            return gettext.translation(
                DOMAIN, localedir=str(directory), languages=["vi"]
            ).gettext
        except FileNotFoundError:
            continue
    return gettext.NullTranslations().gettext
