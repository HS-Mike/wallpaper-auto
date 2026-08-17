"""
Wallpaper Auto - Automatically switch Windows desktop wallpapers based on conditions.
"""

from .evaluator.base_evaluator import BaseEvaluator
from .models import LogLevel
from .resource.base_resource import BaseResource
from .resource_manager import ResourceManager
from .rule_engine import RuleEngine
from .trigger.base_trigger import BaseThreadTrigger, BaseTrigger
from .trigger_manager import TriggerManager

try:
    from ._version import version as __version__
except ImportError:
    try:
        from importlib.metadata import version

        __version__ = version("wallpaper-auto")
    except Exception:
        __version__ = "unknown"


def init_config(output: str, force: bool = False) -> None:
    """Generate a starter YAML config file.

    Lazy wrapper that defers the full service import (and its Qt
    dependency) until actually called. See
    :func:`wallpaper_auto.service.init_config` for the full
    documentation.
    """
    from .service import init_config as _init_config  # noqa: PLC0415

    _init_config(output, force=force)


def run(
    config_path: str,
    log_level: LogLevel | None = None,
    log_file: str | None = None,
    custom_triggers: dict[str, type[BaseTrigger]] | None = None,
    custom_resources: dict[str, type[BaseResource]] | None = None,
    custom_evaluators: dict[str, BaseEvaluator] | None = None,
) -> None:
    """Start the wallpaper auto service.

    Lazy wrapper that defers the full service import (and its Qt
    dependency) until actually called. See
    :func:`wallpaper_auto.service.run` for the full documentation
    and parameter reference.
    """
    from .service import run as _run  # noqa: PLC0415

    _run(
        config_path,
        log_level=log_level,
        log_file=log_file,
        custom_triggers=custom_triggers,
        custom_resources=custom_resources,
        custom_evaluators=custom_evaluators,
    )


__all__ = [
    "BaseTrigger",
    "BaseThreadTrigger",
    "BaseResource",
    "BaseEvaluator",
    "TriggerManager",
    "ResourceManager",
    "RuleEngine",
    "init_config",
    "run",
]

