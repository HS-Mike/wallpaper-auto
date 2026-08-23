"""
Per-subcommand service entry functions for the wallpaper auto.

Each CLI subcommand has a dedicated entry function here:

- :func:`init_config` — the ``init-config`` subcommand: generate a starter
  YAML config.
- :func:`run` — the ``run`` subcommand and the programmatic API:
  configure logging, register optional custom components, then boot the
  controller and system tray.
- :func:`display_capability` — the ``display-capability`` subcommand:
  print current display attributes and per-display capabilities.

The CLI entry point that dispatches to these lives in
:mod:`wallpaper_auto.cli`.

Usage (programmatic)::

    from wallpaper_auto import run

    # With built-in components only
    run("config.yaml")

    # With custom components and logging
    run(
        "config.yaml",
        log_level="INFO",
        log_file="app.log",
        custom_triggers={"my_trigger": MyTrigger},
        custom_resources={"my_resource": MyResource},
        custom_evaluators={"my_evaluator": MyEvaluator()},
    )
"""

from __future__ import annotations

import logging
import sys

import yaml

from .evaluator.base_evaluator import BaseEvaluator
from .models import LoggingConfig, LogLevel
from .resource.base_resource import BaseResource
from .resource_manager import ResourceManager
from .rule_engine import RuleEngine
from .system_tray import WallpaperSwitchSystemTray
from .trigger.base_trigger import BaseTrigger
from .trigger_manager import TriggerManager
from .util.display_util import (
    LUID,
    DisplayInfo,
    get_display_capability,
    get_display_info,
    set_process_dpi_aware,
)
from .wallpaper_controller import WallpaperController

_LOG_FORMAT = "%(asctime)s  %(module)-25s  %(levelname)-7s  %(thread)-6d  %(message)s"


def _setup_logging(level: LogLevel, log_file: str | None = None) -> None:
    """Configure the root logger for the service.

    Writes to the console (default stream handler) and, when given, to a log
    file.  The file handler is thread-safe: ``logging.Handler.emit()`` is
    serialized by an internal lock, so concurrent log calls from the app's
    threads do not interleave writes.

    Args:
        level: Logging level name (``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
            or ``"ERROR"``).
        log_file: Path to the log file; ``None`` logs to the console only.
    """
    logging.basicConfig(level=getattr(logging, level), format=_LOG_FORMAT)
    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(getattr(logging, level))
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logging.getLogger().addHandler(file_handler)


def _read_logging_config(config_path: str) -> LoggingConfig:
    """Read only the ``logging`` section from the config file.

    A minimal pre-parse that runs before the controller exists so logging
    can be configured at the very start of the program.  The authoritative
    full-config parse is still performed later by ``ConfigStore.load``.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        The logging settings, defaulting when the block is absent.
    """
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return LoggingConfig()
    logging_data = data.get("logging")
    if not isinstance(logging_data, dict):
        return LoggingConfig()
    return LoggingConfig(**logging_data)


def init_config(output: str, force: bool = False) -> None:
    """Handle the ``init-config`` subcommand.

    Generates a starter config template at *output*, exiting with code 1 when
    the file already exists and *force* is not set.

    Args:
        output: Output path for the generated config.
        force: Whether to overwrite an existing file.
    """
    from .init_config import generate_template

    try:
        generate_template(output, force=force)
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def run(
    config_path: str,
    log_level: LogLevel | None = None,
    log_file: str | None = None,
    custom_triggers: dict[str, type[BaseTrigger]] | None = None,
    custom_resources: dict[str, type[BaseResource]] | None = None,
    custom_evaluators: dict[str, BaseEvaluator] | None = None,
) -> None:
    """Start the wallpaper auto service.

    Configures logging first from the ``logging`` section of the config
    file — before any component work — so no early startup log line is
    dropped.  Then registers any custom components, boots the controller
    (which owns the :class:`~wallpaper_auto.config_store.ConfigStore` and
    loads the configuration), and runs the system tray.  This is the
    dedicated entry function for the ``run`` subcommand and the
    programmatic API.

    The *log_level* and *log_file* arguments take precedence over the
    ``logging`` section of the config file; values omitted here fall back
    to the config file, then to ``"DEBUG"`` / console-only respectively.

    Args:
        config_path: Path to the YAML configuration file.
        log_level: Logging level name (``"DEBUG"``, ``"INFO"``,
            ``"WARNING"``, or ``"ERROR"``).  ``None`` falls back to the
            config file's ``logging.level``, then ``"DEBUG"``.
        log_file: Path to the log file.  ``None`` falls back to the config
            file's ``logging.file``, then console-only logging.
        custom_triggers: Optional mapping of trigger names to trigger
            classes to register before loading the configuration.
        custom_resources: Optional mapping of resource names to resource
            classes to register before loading the configuration.
        custom_evaluators: Optional mapping of evaluator names to
            evaluator instances to register before loading the
            configuration.
    """
    logging_cfg = _read_logging_config(config_path)
    effective_level = log_level if log_level is not None else logging_cfg.level
    effective_file = log_file if log_file is not None else logging_cfg.file
    _setup_logging(effective_level, effective_file)

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


def display_capability() -> None:
    """Print current display attributes and per-display capabilities.

    Read-only query of display topology and capability via
    :mod:`wallpaper_auto.util.display_util`; never changes any display
    setting.  Declares Per-Monitor DPI awareness first so reported scale
    values are physical pixels.
    """
    set_process_dpi_aware()

    displays = get_display_info()
    if displays is None:
        print("cannot query displays")
        return

    print(f"Found {len(displays)} active display(s)\n")
    for index, display in enumerate(displays, 1):
        _print_display(index, display)
        _print_capability(display)
        print()


def _luid_str(adapter_id: LUID) -> str:
    """Render an adapter LUID as ``"LowPart,HighPart"``.

    Args:
        adapter_id: The adapter LUID to render.

    Returns:
        The formatted LUID string.
    """
    return f"{adapter_id.LowPart},{adapter_id.HighPart}"


def _print_display(index: int, display: DisplayInfo) -> None:
    """Print a display's snapshot attributes.

    Args:
        index: 1-based display index.
        display: The display snapshot to print.
    """
    print(f"  [{index}] {display.device_name}")
    print(f"    model           : {display.model!r}")
    print(f"    source res      : {display.source_resolution[0]} x {display.source_resolution[1]}")
    print(f"    target res      : {display.target_resolution[0]} x {display.target_resolution[1]}")
    print(f"    position        : {display.position[0]}, {display.position[1]}")
    scale = f"{display.scale}%" if display.scale is not None else "n/a"
    print(f"    scale           : {scale}")
    print(f"    monitor path    : {display.monitor_device_path or 'n/a'}")
    print(f"    adapter id      : {_luid_str(display.adapter_id)}")
    print(f"    source id       : {display.source_id}")


def _print_capability(display: DisplayInfo) -> None:
    """Print a display's supported scale percentages and resolutions.

    Args:
        display: The display whose capability to print.
    """
    try:
        capability = get_display_capability(
            display.device_name,
            display.adapter_id,
            display.source_id,
        )
    except (OSError, ValueError) as e:
        print(f"    capability      : unavailable ({e})")
        return
    if capability is None:
        print("    capability      : unavailable (topology transition / driver)")
        return

    scales = ", ".join(f"{s}%" for s in capability.scale)
    resolutions = ", ".join(f"{w}x{h}" for w, h in capability.resolution)
    print("    capability:")
    print(f"      reference scale : {capability.reference_scale}%")
    print(f"      supported scale : {scales}")
    print(f"      resolutions     : {resolutions}")
