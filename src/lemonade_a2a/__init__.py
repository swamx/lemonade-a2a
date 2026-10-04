"""Lemonade A2A: expose local Lemonade inference through Agent2Agent."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lemonade-a2a")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
