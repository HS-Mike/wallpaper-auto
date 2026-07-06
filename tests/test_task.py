"""Tests for task.py — BaseTask __hash__ and task type coverage."""

import pytest

from wallpaper_auto.task import (
    BaseTask,
    Mode,
    ModeSwitchTask,
    PlotCanvasTask,
    QuitTask,
    TaskType,
    TargetSetTask,
)
from wallpaper_auto.models import Rule, ConditionNode


class TestBaseTaskHash:
    """The __hash__ method returns the task's id."""

    def test_hash_returns_id(self):
        task = QuitTask()
        assert task.__hash__() == task.id

    def test_hash_uses_default_uuid_int_id(self):
        task = QuitTask()
        # uuid.uuid4().int produces a 128-bit positive int.
        assert isinstance(task.id, int)
        assert task.id > 0

    def test_two_tasks_have_different_ids(self):
        a = QuitTask()
        b = QuitTask()
        assert a.id != b.id
        assert a.__hash__() != b.__hash__()

    def test_hash_inherits_to_subclasses(self):
        switch = ModeSwitchTask(target_mode=Mode.AUTO)
        target = TargetSetTask(target="r1", matched_rule=None)
        plot = PlotCanvasTask()
        assert switch.__hash__() == switch.id
        assert target.__hash__() == target.id
        assert plot.__hash__() == plot.id


class TestTaskType:
    def test_task_type_values(self):
        assert TaskType.QUIT.value == 0
        assert TaskType.MODE_SWITCH.value == 1
        assert TaskType.TARGET_SET.value == 2
        assert TaskType.PLOT_CANVAS.value == 3


class TestMode:
    def test_mode_values(self):
        assert Mode.AUTO.value == "auto"
        assert Mode.MANUAL.value == "manual"
        assert Mode.UNSET.value == "unset"
