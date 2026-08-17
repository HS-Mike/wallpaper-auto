"""
Command-line interface entry point for the wallpaper auto.

Parses ``sys.argv`` to select a subcommand and dispatches to that
subcommand's dedicated entry function in :mod:`wallpaper_auto.service`:

- ``init-config`` → :func:`wallpaper_auto.service.init_config`
- ``run`` → :func:`wallpaper_auto.service.run`

A subcommand is required; invoking ``wallpaper-auto`` without one prints the
usage and exits.

The ``-l``/``--log-level`` and ``--log-file`` flags are shared, available on
every subcommand both before and after the subcommand name.  When omitted,
values fall back to the config file's ``logging`` section.

Usage::

    python -m wallpaper_auto run -c config.yaml -l INFO

    python -m wallpaper_auto init-config my_config.yaml
"""

from __future__ import annotations

import argparse

from .service import init_config, run

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _add_logging_args(parser: argparse.ArgumentParser, *, suppress_default: bool = False) -> None:
    """Attach the shared logging flags to a parser.

    Args:
        parser: Parser to attach ``-l``/``--log-level`` and ``--log-file`` to.
        suppress_default: Omit the default so a value already set on the
            namespace (e.g. by the top-level parser) is preserved.  Used on
            subparser copies so flags given before the subcommand are not
            overwritten.
    """
    parser.add_argument(
        "-l",
        "--log-level",
        default=argparse.SUPPRESS if suppress_default else None,
        choices=_LOG_LEVELS,
        help="Logging level (config file logging.level used if omitted)",
    )
    parser.add_argument(
        "--log-file",
        default=argparse.SUPPRESS if suppress_default else None,
        help="Path to log file (config file logging.file used if omitted)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wallpaper-auto")

    # Shared logging flags, valid both before and after the subcommand name.
    _add_logging_args(parser)

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    init_config_parser = subparsers.add_parser(
        "init-config",
        help="Generate a starter YAML config file",
    )
    init_config_parser.add_argument(
        "output",
        nargs="?",
        default="config.yaml",
        help="Output path for the generated config (default: config.yaml)",
    )
    init_config_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite existing file without prompting",
    )
    _add_logging_args(init_config_parser, suppress_default=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Start the wallpaper auto service",
    )
    run_parser.add_argument("-c", "--config", default="config.yaml", help="Path to config file")
    _add_logging_args(run_parser, suppress_default=True)

    return parser


def cli() -> None:
    """Entry point for the wallpaper-auto CLI.

    Parses ``sys.argv`` and dispatches to the matching subcommand's
    dedicated entry function.
    """
    from .process_mutex import ProcessMutex  # noqa: PLC0415

    parser = _build_parser()
    args = parser.parse_args()

    if args.subcommand == "init-config":
        init_config(args.output, force=args.force)

    elif args.subcommand == "run":
        with ProcessMutex("wallpaper_auto", raise_error=False):
            run(args.config, log_level=args.log_level, log_file=args.log_file)

    else:
        raise RuntimeError("invalid args")
