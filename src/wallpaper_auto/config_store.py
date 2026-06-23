"""
YAML configuration file loader and validator.

Loads the config from disk, parses it into typed Pydantic models, and exposes
properties for accessing resources, triggers, rules, and fallback settings.
"""

import logging
from pathlib import Path

import yaml

from .models import ConfigModel, ResourceConfig, Rule, TriggerConfig
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

    @property
    def cache(self) -> bool | str | None:
        assert self.config is not None
        return self.config.cache

    @property
    def cache_path(self) -> Path | None:
        assert self.config is not None
        return self.config.cache_path

    def ensure_cache_dir(self) -> Path | None:
        """Create the cache directory if caching is enabled and return its path."""
        cp = self.cache_path
        if cp is not None:
            cp.mkdir(parents=True, exist_ok=True)
        return cp
