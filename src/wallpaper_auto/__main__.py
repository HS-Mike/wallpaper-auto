"""
Entry point for the wallpaper auto CLI.

Thin wrapper that delegates to :func:`wallpaper_auto.cli.cli`.
"""

from .cli import cli

if __name__ == "__main__":
    cli()
