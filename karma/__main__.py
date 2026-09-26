"""Allow ``python -m karma``."""

from karma.cli import main

# The guard matters: worker processes re-import __main__ on spawn-based platforms.
if __name__ == "__main__":
    raise SystemExit(main())
