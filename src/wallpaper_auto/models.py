"""
Pydantic data models for the wallpaper auto configuration.

Includes models for triggers, resources, rules (with AND/OR condition trees),
and the top-level config. Validates that all rule targets reference existing resources.
"""

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .image_cache import CACHE_EVICT_TARGET_RATIO, CACHE_MAX_SIZE_BYTES
from .util.wallpaper_util import WallpaperStyle

DEFAULT_CACHE_DIR = Path.home() / "AppData" / "Local" / "wallpaper-auto" / "cache"


class CacheResizeConfig(BaseModel):
    """Tuning knobs for the resized-image cache (``ImageCompressionCache``).

    Defaults derive from the ``image_cache`` module constants, so an empty
    ``resize`` block enables the cache with standard tuning.
    """

    enabled: bool = True
    max_size_mb: int = CACHE_MAX_SIZE_BYTES // (1024 * 1024)
    evict_ratio: float = CACHE_EVICT_TARGET_RATIO


class CacheConfig(BaseModel):
    """Cache directory plus resized-image cache tuning.

    ``path`` is the shared cache dir used by both the composited wallpaper
    and the resized per-display images. ``resize`` configures the
    ``ImageCompressionCache`` component specifically.
    """

    path: str | None = None
    resize: CacheResizeConfig = CacheResizeConfig()


class TriggerConfig(BaseModel):
    name: str
    config: dict[str, Any] = {}


class ResourceConfig(BaseModel):
    name: str
    config: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def set_single_arg_default(cls, data: Any) -> dict[str, Any]:
        """Coerce a bare image path string into a ``static_wallpaper`` resource config.

        Args:
            data: Raw config value — a config dict, or a single image path string.

        Returns:
            A ``{"name": "static_wallpaper", "config": {...}}`` dict.

        Raises:
            TypeError: If ``data`` is neither a dict nor a string.
        """
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            cfg = {"path": data, "style": WallpaperStyle.FILL}
            return {"name": "static_wallpaper", "config": cfg}
        raise TypeError(f"ResourceConfig data must be a dict or string, got {type(data).__name__}")


class ConditionNode(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    and_conditions: list["ConditionNode"] | None = Field(default=None, alias="and")
    or_conditions: list["ConditionNode"] | None = Field(default=None, alias="or")

    @model_validator(mode="before")
    @classmethod
    def validate_single_key(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if not data:
            raise ValueError("empty node")
        if len(data) != 1:
            raise ValueError("must provide only one key")
        key, value = next(iter(data.items()))
        if key in ("and", "or") and value is None:
            raise ValueError(f"'{key}' must not be null")
        return data

    @model_validator(mode="after")
    def check_extra_structure(self) -> "ConditionNode":
        if self.is_and:
            if not self.and_conditions:
                raise ValueError("'and' must have at least one element")
        elif self.is_or:
            if not self.or_conditions:
                raise ValueError("'or' must have at least one element")
        return self

    @property
    def is_and(self) -> bool:
        return self.and_conditions is not None

    @property
    def is_or(self) -> bool:
        return self.or_conditions is not None

    @property
    def evaluator(self) -> str:
        if self.is_and or self.is_or:
            raise ValueError("and/or node invalid access")
        return next(iter(self.model_extra.keys()))  # type: ignore

    @property
    def evaluator_param(self) -> dict[str, Any]:
        if self.is_and or self.is_or:
            raise ValueError("and/or node invalid access")
        return next(iter(self.model_extra.values()))  # type: ignore


class SceneBinding(BaseModel):
    """A single display-scene binding: which display model → which resource."""

    display_model: str | None = None
    match_display_model: str | None = None
    resource: str
    resolution: tuple[int, int] | None = None
    scale: int | None = None

    @model_validator(mode="after")
    def check_display_model(self) -> "SceneBinding":
        if self.display_model is None and self.match_display_model is None:
            raise ValueError("Either display_model or match_display_model must be specified")
        elif self.display_model is not None and self.match_display_model is not None:
            raise ValueError("Only one of display_model or match_display_model can be specified")
        return self

    @field_validator("scale", mode="before")
    @classmethod
    def validate_scale(cls, v: Any) -> Any:
        """Normalize a decimal scale factor to its integer percent form.

        A config value like ``1.5`` is stored as ``150`` so the scene can be
        matched against integer display scale percentages.

        Args:
            v: Raw ``scale`` value from the config.

        Returns:
            Integer percent for decimal input; otherwise the value unchanged.
        """
        if isinstance(v, float) and 0.99 < v < 5.01:
            return int(round(v * 100, 0))
        return v

    @field_validator("resolution", mode="before")
    @classmethod
    def parse_resolution(cls, v: Any) -> Any:
        if isinstance(v, str):
            # match "1920x1080"、"1920*1080"、"1920, 1080"
            parts = re.split(r"[xX*,\s]+", v.strip())
            if len(parts) == 2:
                return (int(parts[0]), int(parts[1]))
        return v


class Rule(BaseModel):
    name: str
    condition: ConditionNode
    target: str


class ConfigModel(BaseModel):
    resource: dict[str, ResourceConfig] = Field(alias="resource")
    scene: dict[str, list[SceneBinding]] | None = None
    trigger: list[TriggerConfig]
    rule: list[Rule]
    fallback_target: str
    at_shutdown: str | None = None
    cache: CacheConfig = CacheConfig()

    @property
    def cache_path(self) -> Path:
        path_str = self.cache.path
        if path_str is None:
            return DEFAULT_CACHE_DIR
        p = Path(path_str)
        if p.exists() and not p.is_dir():
            raise NotADirectoryError(f"cache path '{path_str}' is not a directory")
        # A missing directory is allowed — callers create it on use.
        return p

    @model_validator(mode="after")
    def check_target_exist(self) -> "ConfigModel":
        if self.fallback_target not in self.resource.keys():
            raise ValueError(f"Fallback target '{self.fallback_target}' not found in resource")
        scene_keys = set(self.scene or {})
        for rule in self.rule:
            if rule.target not in self.resource and rule.target not in scene_keys:
                msg = f"Rule '{rule.name}' targets unknown resource or scene: {rule.target}"
                raise ValueError(msg)
        if self.at_shutdown is not None and self.at_shutdown not in self.resource:
            raise ValueError(f"at_shutdown target '{self.at_shutdown}' not found in resource")
        return self

    @field_validator("scene")
    @classmethod
    def validate_scenes(
        cls, scenes: dict[str, list[SceneBinding]] | None
    ) -> dict[str, list[SceneBinding]] | None:
        if not scenes:
            return scenes

        for scene_name, bindings in scenes.items():
            seen_display: set[str] = set()
            seen_match: set[str] = set()

            for item in bindings:
                if item.display_model:
                    if item.display_model in seen_display:
                        raise ValueError(
                            f"Scene '{scene_name}' has duplicate display_model: "
                            f"'{item.display_model}'"
                        )
                    seen_display.add(item.display_model)

                if item.match_display_model:
                    if item.match_display_model in seen_match:
                        raise ValueError(
                            f"Scene '{scene_name}' has duplicate match_display_model: "
                            f"'{item.match_display_model}'"
                        )
                    seen_match.add(item.match_display_model)

        return scenes


ConditionNode.model_rebuild()
