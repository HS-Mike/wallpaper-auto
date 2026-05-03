"""
Entry point for the wallpaper automator CLI.

Sets up logging, parses command-line arguments, and either launches the
wallpaper controller or runs a utility subcommand (e.g. ``init-config``).
"""

import argparse
import logging
import sys

from .init_config import generate_template
from .service import run_service


def _setup_logging(level: str) -> None:
    """Configure the root logger for the CLI."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s  %(levelname)-7s  %(thread)-6d  %(message)s",
    )


if __name__ == "__main__":
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

    args = parser.parse_args()
    _setup_logging(args.log_level)

    if args.subcommand == "init-config":
        try:
            generate_template(args.output, force=args.force)
        except FileExistsError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        run_service(args.config)
