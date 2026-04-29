import pytest

from wallpaper_automator.util.callback_register import CallbackRegister


class TestCallbackRegister:
    """Direct tests for the CallbackRegister base class."""

    def test_add_callback(self):
        cb = CallbackRegister()
        cb.add_callback(lambda: None)
        assert len(cb._callbacks) == 1

    def test_add_multiple_callbacks(self):
        cb = CallbackRegister()
        cb.add_callback(lambda: None)
        cb.add_callback(lambda: None)
        assert len(cb._callbacks) == 2

    def test_remove_callback(self):
        cb = CallbackRegister()
        handler = lambda: None
        cb.add_callback(handler)
        cb.remove_callback(handler)
        assert len(cb._callbacks) == 0

    def test_clear_callback(self):
        cb = CallbackRegister()
        cb.add_callback(lambda: None)
        cb.add_callback(lambda: None)
        cb.clear_callback()
        assert len(cb._callbacks) == 0

    def test_trigger_callback_fires_all_handlers(self):
        cb = CallbackRegister()
        results = []
        cb.add_callback(lambda: results.append("a"))
        cb.add_callback(lambda: results.append("b"))
        cb.trigger_callback()
        assert results == ["a", "b"]

    def test_trigger_callback_returns_results(self):
        cb = CallbackRegister()
        cb.add_callback(lambda: 1)
        cb.add_callback(lambda: 2)
        results = cb.trigger_callback()
        assert results == [1, 2]

    def test_trigger_callback_with_args(self):
        cb = CallbackRegister()
        received = []
        cb.add_callback(lambda x, y: received.append((x, y)))
        cb.trigger_callback(1, "hello")
        assert received == [(1, "hello")]

    def test_trigger_callback_with_kwargs(self):
        cb = CallbackRegister()
        received = []
        cb.add_callback(lambda x=None: received.append(x))
        cb.trigger_callback(x=42)
        assert received == [42]

    def test_add_non_callable_raises(self):
        cb = CallbackRegister()
        with pytest.raises(ValueError):
            cb.add_callback("not a function")

    def test_remove_nonexistent_raises(self):
        cb = CallbackRegister()
        with pytest.raises(ValueError):
            cb.remove_callback(lambda: None)
