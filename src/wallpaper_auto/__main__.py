"""
Entry point for the wallpaper auto CLI.

Thin wrapper that delegates to :func:`wallpaper_auto.service.run_service`.
"""

from .service import run_service

if __name__ == "__main__":
    run_service()
