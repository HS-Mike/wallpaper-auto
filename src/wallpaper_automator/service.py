"""
Service entry point for the wallpaper automator.

Provides :func:`run_service` which orchestrates the full startup sequence
(controller creation, config loading, system tray setup, worker loop, signal
handling) and accepts optional custom component registrations.

Usage::

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

from typing import Optional

from .evaluator.base_evaluator import BaseEvaluator
from .resource.base_resource import BaseResource
from .resource_manager import ResourceManager
from .rule_engine import RuleEngine
from .system_tray import WallpaperSwitchSystemTray
from .trigger.base_trigger import BaseTrigger
from .trigger_manager import TriggerManager
from .wallpaper_controller import WallpaperController


def run_service(
    config_path: str,
    custom_triggers: Optional[dict[str, type[BaseTrigger]]] = None,  # noqa: UP045
    custom_resources: Optional[dict[str, type[BaseResource]]] = None,  # noqa: UP045
    custom_evaluators: Optional[dict[str, BaseEvaluator]] = None,  # noqa: UP045
) -> None:
    """Start the wallpaper automator service.

    Parameters
    ----------
    config_path:
        Path to the YAML configuration file.
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
    if custom_triggers is not None:
        for name, cls in custom_triggers.items():
            TriggerManager.register_trigger(name, cls)

    if custom_resources is not None:
        for name, cls in custom_resources.items():
            ResourceManager.register_resource(name, cls)

    if custom_evaluators is not None:
        for name, instance in custom_evaluators.items():
            RuleEngine.register_evaluator(name, instance)

    controller = WallpaperController()
    controller.load_config(config_path)

    tray = WallpaperSwitchSystemTray()
    controller.set_tray(tray)
    controller.start()
    tray.exec()
