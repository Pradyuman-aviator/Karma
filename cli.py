"""Backward-compatible entry point: ``python cli.py run ...``.

Prefer the installed ``karma`` command or ``python -m karma``.
"""

from karma.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
