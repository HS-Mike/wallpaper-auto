"""Tests for task.py — task __hash__ and task type coverage."""
from wallpaper_auto.task import (
    Mode,
    ModeSwitchTask,
    PlotCanvasTask,
    QuitTask,
    TargetSetTask,
)


class TestBaseTaskHash:
    """The __hash__ method returns the task's id."""

    def test_hash_returns_id(self):
        task = QuitTask()
        assert hash(task) == task.id

    def test_hash_uses_default_uuid_int_id(self):
        task = QuitTask()
        # uuid.uuid4().int produces a 128-bit positive int.
        assert isinstance(task.id, int)
        assert task.id > 0

    def test_two_tasks_have_different_ids(self):
        a = QuitTask()
        b = QuitTask()
        assert a.id != b.id
        assert hash(a) != hash(b)

    def test_hash_and_dunder_hash_agree(self):
        """hash() must return the same value as __hash__().

        Python's built-in hash() can return a truncated value for very large
        ints (those exceeding Py_ssize_t width). Since __hash__ returns self.id
        directly, verify that hash() doesn't silently mangle the value.
        """
        task = QuitTask()
        assert hash(task) == task.__hash__()

    def test_hash_consistent_across_types(self):
        switch = ModeSwitchTask(target_mode=Mode.AUTO)
        target = TargetSetTask(target="r1", matched_rule=None)
        plot = PlotCanvasTask()
        assert hash(switch) == switch.id
        assert hash(target) == target.id
        assert hash(plot) == plot.id
