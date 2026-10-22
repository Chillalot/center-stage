"""Cross-platform desktop entry point."""

from __future__ import annotations

import os
import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    if os.name == "nt":
        from .gui_qt import main as gui_main
    else:
        from .gui import main as gui_main
    return gui_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
