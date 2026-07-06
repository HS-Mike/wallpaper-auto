"""
Task classes transmit across components.
"""
import uuid
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import Rule


class Mode(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    UNSET = "unset"


class TaskType(Enum):
    QUIT = 0
    MODE_SWITCH = 1
    TARGET_SET = 2
    PLOT_CANVAS = 3


class BaseTask(BaseModel):
    id: int = Field(default_factory=lambda: uuid.uuid4().int)
    model_config = ConfigDict(extra="allow", frozen=True)

    def __hash__(self) -> int:
        return self.id


class QuitTask(BaseTask):
    type: Literal[TaskType.QUIT] = TaskType.QUIT


class ModeSwitchTask(BaseTask):
    type: Literal[TaskType.MODE_SWITCH] = TaskType.MODE_SWITCH
    target_mode: Mode


class TargetSetTask(BaseTask):
    type: Literal[TaskType.TARGET_SET] = TaskType.TARGET_SET
    target: str
    matched_rule: Rule | None


class PlotCanvasTask(BaseTask):
    type: Literal[TaskType.PLOT_CANVAS] = TaskType.PLOT_CANVAS


Task = Annotated[QuitTask | ModeSwitchTask | TargetSetTask | PlotCanvasTask, Field(discriminator="type")]
