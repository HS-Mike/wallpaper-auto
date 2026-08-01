"""
YAML configuration file loader and validator.

Loads the config from disk, parses it into typed Pydantic models, and exposes
properties for accessing resources, triggers, rules, and fallback settings.
"""

import logging
from pathlib import Path

import yaml

from .models import CacheConfig, ConfigModel, ResourceConfig, Rule, SceneBinding, TriggerConfig
from .util.singleton_meta import SingletonMeta

logger = logging.getLogger(__name__)


class ConfigStore(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self.config: ConfigModel | None = None

    def load(self, config_path: str) -> None:
        with open(config_path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
        self.config = ConfigModel(**raw_data)

    @property
    def fallback_target(self) -> str:
        assert self.config is not None
        return self.config.fallback_target

    @property
    def at_shutdown_target(self) -> str | None:
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

    @property
    def cache_path(self) -> Path:
        assert self.config is not None
        return self.config.cache_path

    @property
    def cache(self) -> CacheConfig:
        assert self.config is not None
        return self.config.cache

    @property
    def scene(self) -> dict[str, list[SceneBinding]]:
        assert self.config is not None
        return self.config.scene or {}
