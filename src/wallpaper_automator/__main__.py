"""
Entry point for the wallpaper automator CLI.

Thin wrapper that delegates to :func:`wallpaper_automator.service.run_service`.
"""

from .service import run_service

if __name__ == "__main__":
    run_service()
