"""Tests for base_resource.py — BaseResource abstract class."""

import pytest
from PIL import Image

from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.util.wallpaper_util import WallpaperStyle


class MockResource(BaseResource):
    """Concrete BaseResource subclass for testing abstract methods."""

    def mount(self) -> None:
        pass

    def demount(self) -> None:
        pass


class TestBaseResource:
    def test_abstract_methods(self):
        """BaseResource cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseResource.__new__(BaseResource)  # type: ignore[no-untyped-call]


class TestBindMonitorDevicePath:
    def test_bind_sets_path(self):
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        assert r.monitor_device_path == "MONITOR\\1"

    def test_rebind_raises(self):
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        with pytest.raises(RuntimeError, match="monitor_device_path already bound"):
            r._bind_monitor_device_path("MONITOR\\2")


class TestBindPlotCanvas:
    def test_bind_sets_canvas(self):
        r = MockResource()
        canvas = lambda *a, **kw: None
        r._bind_plot_canvas(canvas)
        assert r._plot_canvas is canvas

    def test_rebind_raises(self):
        r = MockResource()
        r._bind_plot_canvas(lambda *a, **kw: None)
        with pytest.raises(RuntimeError, match="plot_canvas already bound"):
            r._bind_plot_canvas(lambda *a, **kw: None)


class TestUnbindPlotCanvas:
    def test_unbind_clears_canvas(self):
        r = MockResource()
        r._bind_plot_canvas(lambda *a, **kw: None)
        r._unbind_plot_canvas()
        assert r._plot_canvas is None

    def test_unbind_without_bind_raises(self):
        r = MockResource()
        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
            r._unbind_plot_canvas()

    def test_double_unbind_raises(self):
        r = MockResource()
        r._bind_plot_canvas(lambda *a, **kw: None)
        r._unbind_plot_canvas()
        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
            r._unbind_plot_canvas()


class TestPlotCanvas:
    def test_dispatches_to_bound_canvas(self):
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        captured = []

        def canvas(monitor_id, style, image, immediate_update):
            captured.append((monitor_id, style, image, immediate_update))

        r._bind_plot_canvas(canvas)
        img = Image.new("RGB", (4, 4))
        r.plot_canvas(WallpaperStyle.FILL, img, immediate_update=True)

        assert captured == [("MONITOR\\1", WallpaperStyle.FILL, img, True)]

    def test_raises_when_canvas_unbound(self):
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
            r.plot_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))

    def test_raises_when_monitor_path_unbound(self):
        r = MockResource()
        r._bind_plot_canvas(lambda *a, **kw: None)
        with pytest.raises(RuntimeError, match="monitor_device_path not bound"):
            r.plot_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))
