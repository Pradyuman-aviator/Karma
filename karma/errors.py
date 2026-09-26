"""Exception types Karma raises for expected, user-facing failures."""

from __future__ import annotations


class KarmaError(Exception):
    """An expected failure, reported to the user as a message rather than a traceback."""


class GitError(KarmaError):
    """A git command failed or the repository is not in a usable state."""


class ConfigError(KarmaError):
    """The ``[tool.karma]`` configuration is invalid."""
