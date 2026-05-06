"""
Wallpaper Automator - Automatically switch Windows desktop wallpapers based on conditions.
"""

from typing import Literal, Optional

from .evaluator.base_evaluator import BaseEvaluator
from .resource.base_resource import BaseResource
from .resource_manager import ResourceManager
from .rule_engine import RuleEngine
from .trigger.base_trigger import BaseThreadTrigger, BaseTrigger
from .trigger_manager import TriggerManager

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
    config_path: Optional[str] = None,
    *,
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG",
    custom_triggers: Optional[dict[str, type[BaseTrigger]]] = None,
    custom_resources: Optional[dict[str, type[BaseResource]]] = None,
    custom_evaluators: Optional[dict[str, BaseEvaluator]] = None,
) -> None:
    """Start the wallpaper automator service.

    Lazy wrapper that defers the full service import (and its Qt
    dependency) until actually called. See
    :func:`wallpaper_automator.service.run_service` for the full
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
