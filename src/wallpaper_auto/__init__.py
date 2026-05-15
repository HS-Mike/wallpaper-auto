"""
Wallpaper Auto - Automatically switch Windows desktop wallpapers based on conditions.
"""

from typing import Literal

from .evaluator.base_evaluator import BaseEvaluator
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


__all__ = [
    "run_service",
    "BaseTrigger",
    "BaseThreadTrigger",
    "BaseResource",
    "BaseEvaluator",
    "TriggerManager",
    "ResourceManager",
    "RuleEngine",
]


def run_service(
    config_path: str | None = None,
    *,
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG",
    custom_triggers: dict[str, type[BaseTrigger]] | None = None,
    custom_resources: dict[str, type[BaseResource]] | None = None,
    custom_evaluators: dict[str, BaseEvaluator] | None = None,
) -> None:
    """Start the wallpaper auto service.

    Lazy wrapper that defers the full service import (and its Qt
    dependency) until actually called. See
    :func:`wallpaper_auto.service.run_service` for the full
    documentation and parameter reference.
    """
    from .service import run_service as _run_service  # noqa: PLC0415

    return _run_service(
        config_path,
        log_level=log_level,
        custom_triggers=custom_triggers,
        custom_resources=custom_resources,
        custom_evaluators=custom_evaluators,
    )
