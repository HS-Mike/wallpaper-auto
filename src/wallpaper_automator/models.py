"""
Pydantic data models for the wallpaper automator configuration.

Includes models for triggers, resources, rules (with AND/OR condition trees),
and the top-level config. Validates that all rule targets reference existing resources.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .resource.wallpaper_utils import WallpaperStyle


class TriggerConfig(BaseModel):
    name: str
    config: dict[str, Any] = {}


class ResourceConfig(BaseModel):
    name: str
    config: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def set_single_arg_default(cls, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            cfg = {"path": data, "style": WallpaperStyle.FILL}
            return {"name": "static_wallpaper", "config": cfg}
        raise TypeError(
            f"ResourceConfig data must be a dict or string, got {type(data).__name__}"
        )


class ConditionNode(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    and_conditions: list["ConditionNode"] | None = Field(default=None, alias="and")
    or_conditions: list["ConditionNode"] | None = Field(default=None, alias="or")

    @model_validator(mode="before")
    @classmethod
    def validate_single_key(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if len(data) != 1:
            raise ValueError("must provide only one key")
        return data

    @model_validator(mode="after")
    def check_extra_structure(self) -> "ConditionNode":
        if not self.is_and and not self.is_or:
            if not self.model_extra:
                raise ValueError("empty node")
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


class Rule(BaseModel):
    name: str
    condition: ConditionNode
    target: str


class ConfigModel(BaseModel):
    resource: dict[str, ResourceConfig] = Field(alias="resource")
    trigger: list[TriggerConfig]
    rule: list[Rule]
    fallback: str

    @model_validator(mode="after")
    def check_target_exist(self) -> "ConfigModel":
        if self.fallback not in self.resource.keys():
            raise ValueError(f"Fallback target '{self.fallback}' not found in resource")
        for rule in self.rule:
            if rule.target not in self.resource:
                raise ValueError(f"Rule '{rule.name}' targets unknown resource: {rule.target}")
        return self


ConditionNode.model_rebuild()
