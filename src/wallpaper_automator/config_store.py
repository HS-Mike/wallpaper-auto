"""
YAML configuration file loader and validator.

Loads the config from disk, parses it into typed Pydantic models, and exposes
properties for accessing resources, triggers, rules, and fallback settings.
"""

import logging

import yaml

from .models import ConfigModel, ResourceConfig, Rule, TriggerConfig

logger = logging.getLogger(__name__)


class ConfigStore:
    def __init__(self) -> None:
        self.config: ConfigModel | None = None

    def load(self, config_path: str) -> None:
        with open(config_path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
        self.config = ConfigModel(**raw_data)

    @property
    def fallback_resource_id(self) -> str:
        assert self.config is not None
        return self.config.fallback

    @property
    def at_shutdown_resource_id(self) -> str | None:
        assert self.config is not None
        return self.config.at_shutdown

    @property
    def resource(self) -> dict[str, ResourceConfig]:
        assert self.config is not None
        return self.config.resource

    @property
    def rule(self) -> list[Rule]:
        assert self.config is not None
        return self.config.rule

    @property
    def trigger(self) -> list[TriggerConfig]:
        assert self.config is not None
        return self.config.trigger
