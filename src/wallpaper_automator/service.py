"""
Service entry point for the wallpaper automator.

Provides :func:`run_service` which orchestrates the full startup sequence
(controller creation, config loading, system tray setup, worker loop, signal
handling) and accepts optional custom component registrations.

When called without ``config_path`` (the default), ``run_service`` parses
``sys.argv`` for CLI arguments, including subcommands such as
``init-config``.  When called with an explicit ``config_path`` it behaves
as a purely programmatic API.

Usage (CLI)::

    python -m wallpaper_automator -c config.yaml -l INFO

    python -m wallpaper_automator init-config my_config.yaml

Usage (programmatic)::

    from wallpaper_automator import run_service

    # With built-in components only
    run_service("config.yaml")

    # With custom components
    run_service(
        "config.yaml",
        custom_triggers={"my_trigger": MyTrigger},
        custom_resources={"my_resource": MyResource},
        custom_evaluators={"my_evaluator": MyEvaluator()},
    )

"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Literal, Optional

from .evaluator.base_evaluator import BaseEvaluator
from .resource.base_resource import BaseResource
from .resource_manager import ResourceManager
from .rule_engine import RuleEngine
from .system_tray import WallpaperSwitchSystemTray
from .trigger.base_trigger import BaseTrigger
from .trigger_manager import TriggerManager
from .wallpaper_controller import WallpaperController

_LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _setup_logging(level: _LogLevel) -> None:
    """Configure the root logger for the CLI."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s  %(levelname)-7s  %(thread)-6d  %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""

    parser = argparse.ArgumentParser(prog="wallpaper-automator")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "-l",
        "--log-level",
        default="DEBUG",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    init_parser = subparsers.add_parser(
        "init-config",
        help="Generate a starter YAML config file",
    )
    init_parser.add_argument(
        "output",
        nargs="?",
        default="config.yaml",
        help="Output path for the generated config (default: config.yaml)",
    )
    init_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite existing file without prompting",
    )

    return parser


def run_service(
    config_path: Optional[str] = None,  # noqa: UP045
    log_level: _LogLevel = "DEBUG",
    custom_triggers: Optional[dict[str, type[BaseTrigger]]] = None,  # noqa: UP045
    custom_resources: Optional[dict[str, type[BaseResource]]] = None,  # noqa: UP045
    custom_evaluators: Optional[dict[str, BaseEvaluator]] = None,  # noqa: UP045
) -> None:
    """Start the wallpaper automator service.

    When *config_path* is ``None`` (the default), the function enters CLI
    mode: it parses ``sys.argv``, handles subcommands like ``init-config``,
    wraps the service lifetime in a ``ProcessMutex``, and exits with a
    non-zero return code on errors.

    When *config_path* is provided explicitly the function operates as a
    pure programmatic API — no argument parsing, no mutex — and the caller
    is responsible for any singleton enforcement.

    Parameters
    ----------
    config_path:
        Path to the YAML configuration file.  When ``None`` the function
        parses ``sys.argv`` to determine the config path, log level, and
        optional subcommand.
    log_level:
        Logging level string (``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
        ``"ERROR"``).  Only used when *config_path* is ``None`` (CLI
        mode), or when set programmatically.  If a CLI ``-l`` flag is
        present it takes precedence.
    custom_triggers:
        Optional mapping of trigger names to trigger classes to register
        before loading the configuration.
    custom_resources:
        Optional mapping of resource names to resource classes to register
        before loading the configuration.
    custom_evaluators:
        Optional mapping of evaluator names to evaluator instances to
        register before loading the configuration.
    """
    # ── CLI mode ──────────────────────────────────────────────────────
    if config_path is None:
        from .init_config import generate_template  # noqa: PLC0415
        from .process_mutex import ProcessMutex  # noqa: PLC0415

        parser = _build_parser()
        args = parser.parse_args()

        # Subcommand: init-config
        if args.subcommand == "init-config":
            _setup_logging(args.log_level)
            try:
                generate_template(args.output, force=args.force)
            except FileExistsError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            return

        log_level = args.log_level

        _setup_logging(log_level)

        # Normal run: wrap in process mutex
        try:
            with ProcessMutex("wallpaper_automator"):
                _run_service_impl(
                    args.config,
                    custom_triggers,
                    custom_resources,
                    custom_evaluators,
                )
        except RuntimeError:
            print("Another instance is already running.", file=sys.stderr)
            sys.exit(1)
        return

    # ── Programmatic mode ─────────────────────────────────────────────
    _run_service_impl(
        config_path,
        custom_triggers,
        custom_resources,
        custom_evaluators,
    )


def _run_service_impl(
    config_path: str,
    custom_triggers: dict[str, type[BaseTrigger]] | None = None,
    custom_resources: dict[str, type[BaseResource]] | None = None,
    custom_evaluators: dict[str, BaseEvaluator] | None = None,
) -> None:
    """Shared startup logic used by both CLI and programmatic modes."""

    if custom_triggers is not None:
        for name, trigger_cls in custom_triggers.items():
            TriggerManager.register_trigger(name, trigger_cls)

    if custom_resources is not None:
        for name, resource_cls in custom_resources.items():
            ResourceManager.register_resource(name, resource_cls)

    if custom_evaluators is not None:
        for name, instance in custom_evaluators.items():
            RuleEngine.register_evaluator(name, instance)

    controller = WallpaperController()
    controller.load_config(config_path)

    tray = WallpaperSwitchSystemTray()
    controller.set_tray(tray)
    controller.start()
    tray.exec()
