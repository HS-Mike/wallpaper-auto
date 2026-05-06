"""Tests for trigger_manager.py — trigger lifecycle and callback management."""

import pytest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from wallpaper_automator.trigger_manager import TriggerManager, _BUILTIN_TRIGGERS
from wallpaper_automator.models import TriggerConfig
from wallpaper_automator.trigger.base_trigger import BaseTrigger
from wallpaper_automator.trigger.time_trigger import TimeTrigger


@pytest.fixture
def mgr():
    """Provide a fresh TriggerManager instance for each test."""
    return TriggerManager()


class TestTriggerManagerInit:
    """TriggerManager __init__ and class-level defaults"""

    def test_init_sets_empty_state(self, mgr):
        """A new manager starts with no triggers and unpaused."""
        assert mgr._triggers == []
        assert not mgr._paused.is_set()

    def test_builtin_triggers_are_registered(self):
        """The class-level _support_triggers dict contains all built-in triggers.

        Uses _BUILTIN_TRIGGERS keys to verify, avoiding imports of heavy
        platform-specific trigger modules (wmi, win32gui, etc.) at module load time.
        """
        builtin_keys = set(_BUILTIN_TRIGGERS.keys())
        assert TriggerManager._support_triggers.keys() >= builtin_keys


class TestTriggerManagerRegisterTrigger:
    """TriggerManager.register_trigger class method.

    Registers a trigger class by name so it can be instantiated via init().
    The class must be BaseTrigger or a subclass thereof.
    """

    @pytest.mark.parametrize("trigger_cls", [
        BaseTrigger,
        type("CustomTrigger", (BaseTrigger,), {}),
    ])
    def test_register_valid_class_succeeds(self, trigger_cls):
        """BaseTrigger and its subclasses can be registered."""
        with patch.dict(TriggerManager._support_triggers, clear=False):
            TriggerManager.register_trigger("test", trigger_cls)
            assert TriggerManager._support_triggers["test"] is trigger_cls

    def test_register_non_class_type_error(self):
        """A non-class value raises TypeError from issubclass."""
        with pytest.raises(TypeError, match="issubclass"):
            TriggerManager.register_trigger("bad", "not_a_class")  # type: ignore

    def test_register_non_subclass_class_error(self):
        """A class that does not inherit from BaseTrigger raises ValueError."""
        with pytest.raises(ValueError, match="trigger cls must inherit from BaseTrigger"):
            TriggerManager.register_trigger("bad", object)  # type: ignore


class TestTriggerManagerInitTriggers:
    """TriggerManager.init() — creating trigger instances from config"""

    def test_init_with_single_time_trigger(self, mgr):
        """init creates a TimeTrigger when given a 'time' config entry."""
        mgr.init([TriggerConfig(name="time", config={})])
        assert len(mgr._triggers) == 1
        assert isinstance(mgr._triggers[0], TimeTrigger)

    def test_init_config_kwargs_forwarded_to_constructor(self):
        """The config dict is unpacked as **kwargs to the trigger's __init__."""
        mock_cls = MagicMock(return_value=MagicMock(spec=BaseTrigger))
        with patch.object(TriggerManager, "_support_triggers", {"mock": mock_cls}):
            mgr = TriggerManager()
            mgr.init([TriggerConfig(name="mock", config={"key": "val"})])

        mock_cls.assert_called_once_with(**{"key": "val"})

    def test_init_with_time_trigger_config(self, mgr):
        """Config kwargs flow through to TimeTrigger.__init__()."""
        mgr.init([TriggerConfig(name="time", config={"interval": 60})])
        assert len(mgr._triggers) == 1
        assert isinstance(mgr._triggers[0], TimeTrigger)
        assert mgr._triggers[0]._interval == timedelta(seconds=60)

    def test_init_multiple_triggers(self, mgr):
        """init creates multiple triggers when given multiple config entries."""
        mgr.init([
            TriggerConfig(name="time", config={}),
            TriggerConfig(name="network", config={}),
        ])
        assert len(mgr._triggers) == 2

    def test_init_unknown_trigger_raises(self, mgr):
        """init raises ValueError when the trigger name is not registered."""
        with pytest.raises(ValueError, match="trigger nonexistent not found"):
            mgr.init([TriggerConfig(name="nonexistent", config={})])

    def test_init_empty_config_list(self, mgr):
        """init with an empty list leaves _triggers empty."""
        mgr.init([])
        assert mgr._triggers == []

    def test_init_wires_callback_to_manager(self, mgr):
        """Each trigger's add_callback is wired so firing the trigger calls the manager's chain."""
        fired = []
        mgr.add_callback(lambda: fired.append("ok"))
        mgr.init([TriggerConfig(name="time", config={})])

        mgr._triggers[0].trigger()
        assert fired == ["ok"]


_METHODS = ["activate", "deactivate"]


class TestTriggerManagerActivateDeactivate:
    """TriggerManager.activate() / .deactivate() — life-cycle delegation"""

    @pytest.mark.parametrize("method", _METHODS)
    def test_calls_all_triggers(self, mgr, method):
        """activate/deactivate is forwarded to every registered trigger."""
        mocks = [MagicMock(spec=BaseTrigger) for _ in range(3)]
        mgr._triggers = mocks

        getattr(mgr, method)()
        for m in mocks:
            getattr(m, method).assert_called_once()

    @pytest.mark.parametrize("method", _METHODS)
    def test_with_no_triggers(self, mgr, method):
        """activate/deactivate is a no-op when no triggers are registered (no error)."""
        getattr(mgr, method)()


class TestTriggerManagerPauseResume:
    """TriggerManager.pause() / .resume() — pausing callback execution"""

    def test_pause_sets_event(self, mgr):
        """pause sets the internal _paused event."""
        mgr.pause()
        assert mgr._paused.is_set()

    def test_resume_clears_event(self, mgr):
        """resume clears a previously set _paused event."""
        mgr.pause()
        mgr.resume()
        assert not mgr._paused.is_set()

    @pytest.mark.parametrize("method,check", [
        ("pause", lambda e: e.is_set()),
        ("resume", lambda e: not e.is_set()),
    ])
    def test_is_idempotent(self, mgr, method, check):
        """Calling pause/resume multiple times has no side effects."""
        getattr(mgr, method)()
        getattr(mgr, method)()
        assert check(mgr._paused)


class TestTriggerManagerTriggerCallback:
    """TriggerManager.trigger_callback() — respects pause state"""

    def test_normal_fires_handlers(self, mgr):
        """When not paused, trigger_callback invokes all registered handlers."""
        results = []
        mgr.add_callback(lambda: results.append("a"))
        mgr.add_callback(lambda: results.append("b"))

        mgr.trigger_callback()
        assert results == ["a", "b"]

    def test_paused_blocks_handlers_and_returns_empty(self, mgr):
        """When paused, handlers are not called and an empty list is returned."""
        results = []
        mgr.add_callback(lambda: results.append("x"))

        mgr.pause()
        result = mgr.trigger_callback()
        assert results == []
        assert result == []

    def test_pause_resume_cycle(self, mgr):
        """A full pause/resume cycle correctly gates callback execution."""
        count = 0

        def inc():
            nonlocal count
            count += 1

        mgr.add_callback(inc)

        mgr.trigger_callback()
        assert count == 1

        mgr.pause()
        mgr.trigger_callback()
        assert count == 1

        mgr.resume()
        mgr.trigger_callback()
        assert count == 2


class TestTriggerManagerCallbackManagement:
    """Smoke tests for inherited CallbackRegister functionality.

    These are not exhaustive — the full CallbackRegister test suite lives in
    test_callback_register.py. Here we only verify the methods haven't been
    broken by TriggerManager's inheritance chain or its trigger_callback override.
    """

    def test_add_and_clear_callbacks(self, mgr):
        """add_callback stores handlers; clear_callback removes all of them."""
        mgr.add_callback(lambda: None)
        mgr.add_callback(lambda: None)
        assert len(mgr._callbacks) == 2
        mgr.clear_callback()
        assert len(mgr._callbacks) == 0

    def test_add_remove_callback(self, mgr):
        """A handler added with add_callback can be removed individually."""
        fn = lambda: None
        mgr.add_callback(fn)
        mgr.remove_callback(fn)
        assert len(mgr._callbacks) == 0

    def test_remove_nonexistent_raises(self, mgr):
        """remove_callback raises ValueError for a handler that was never added."""
        with pytest.raises(ValueError):
            mgr.remove_callback(lambda: None)

    def test_add_non_callable_raises(self, mgr):
        """add_callback raises ValueError when given a non-callable object."""
        with pytest.raises(ValueError):
            mgr.add_callback("not_callable")  # type: ignore
