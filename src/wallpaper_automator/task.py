"""
Task classes transmit across components.
"""
import dataclasses
from enum import Enum
from typing import Literal, Annotated, Union

from pydantic import BaseModel, ConfigDict, Field


class Mode(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    UNSET = "unset"


class TaskType(Enum):
    QUIT = 0
    MODE_SWITCH = 1
    RESOURCE_SET = 2


class BaseTask(BaseModel):
    model_config = ConfigDict(extra='allow', frozen=True)

class QuitTask(BaseTask):
    type: Literal[TaskType.QUIT] = TaskType.QUIT

class ModeSwitchTask(BaseTask):
    type: Literal[TaskType.MODE_SWITCH] = TaskType.MODE_SWITCH
    target_mode: Mode


class ResourceSetTask(BaseTask):
    type: Literal[TaskType.RESOURCE_SET] = TaskType.RESOURCE_SET
    target_resource_id: str


Task = Annotated[
    Union[QuitTask, ModeSwitchTask, ResourceSetTask],
    Field(discriminator='type')
]
