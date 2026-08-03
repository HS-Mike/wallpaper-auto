"""Tests for base_resource.py — BaseResource abstract class."""

from pathlib import Path

import pytest
from PIL import Image

from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.util.wallpaper_util import WallpaperStyle


def _noop_canvas(
    monitor_device_path: str,
    style: WallpaperStyle,
    image: Path | Image.Image,
) -> None:
    """Canvas stub that does nothing — matches UpdateCanvasProtocol exactly."""


class MockResource(BaseResource):
    """Concrete BaseResource subclass for testing abstract methods."""

    def mount(self) -> None:
        pass

    def demount(self) -> None:
        pass


class TestMockResource:
    """MockResource fulfils the BaseResource contract."""

    def test_mount_exists(self) -> None:
        """mount() is callable and does not raise."""
        MockResource().mount()

    def test_demount_exists(self) -> None:
        """demount() is callable and does not raise."""
        MockResource().demount()

    def test_is_concrete(self) -> None:
        """MockResource can be instantiated (abstract methods are implemented)."""
        MockResource()


class TestInitState:
    """Initial state after construction."""

    def test_monitor_path_is_none(self) -> None:
        assert MockResource().monitor_device_path is None

    def test_update_canvas_is_none(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_update_canvas", None)
        assert MockResource()._update_canvas is None

    def test_plot_canvas_is_none(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_plot_canvas", None)
        assert MockResource()._plot_canvas is None


class TestBindMonitorDevicePath:
    def test_bind_sets_path(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        assert r.monitor_device_path == "MONITOR\\1"

    def test_rebind_raises(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        with pytest.raises(RuntimeError, match="monitor_device_path already bound"):
            r._bind_monitor_device_path("MONITOR\\2")

    def test_bind_none_path(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        r.monitor_device_path = None
        # After resetting manually, a new bind succeeds
        r._bind_monitor_device_path("MONITOR\\2")
        assert r.monitor_device_path == "MONITOR\\2"


class TestUpdateCanvas:
    """update_canvas buffers an image through the class-wide callback."""

    def test_dispatches_to_callback(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        captured: list[tuple[object, ...]] = []

        def canvas(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
        ) -> None:
            captured.append((monitor_device_path, style, image))

        r._update_canvas = canvas
        img = Image.new("RGB", (4, 4))
        r.update_canvas(WallpaperStyle.FILL, img)

        assert captured == [("MONITOR\\1", WallpaperStyle.FILL, img)]

    def test_with_path_image(self) -> None:
        """update_canvas accepts a pathlib.Path as image argument."""
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        captured: list[object] = []

        def canvas(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
        ) -> None:
            captured.append(image)

        r._update_canvas = canvas
        path = Path("/tmp/wallpaper.png")
        r.update_canvas(WallpaperStyle.STRETCH, path)
        assert captured == [path]
        assert isinstance(captured[0], Path)

    def test_propagates_canvas_exception(self) -> None:
        """Exceptions from the canvas callable propagate to the caller."""
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")

        def canvas(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
        ) -> None:
            raise RuntimeError("canvas failure")

        r._update_canvas = canvas
        with pytest.raises(RuntimeError, match="canvas failure"):
            r.update_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))

    def test_raises_when_canvas_unbound(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_update_canvas", None)
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        with pytest.raises(RuntimeError, match="update_canvas not bound"):
            r.update_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))

    def test_raises_when_monitor_path_unbound(self) -> None:
        r = MockResource()
        r._update_canvas = _noop_canvas
        with pytest.raises(RuntimeError, match="monitor_device_path not bound"):
            r.update_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))


class TestPlotCanvas:
    """plot_canvas requests a composite through the class-wide callback."""

    def test_dispatches_to_class_callback(self, monkeypatch) -> None:
        called: list[bool] = []
        # staticmethod wrapper — a plain function stored on the class would be
        # bound as a method on instance access.
        monkeypatch.setattr(BaseResource, "_plot_canvas", staticmethod(lambda: called.append(True)))
        MockResource().plot_canvas()
        assert called == [True]

    def test_raises_when_no_callback_registered(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_plot_canvas", None)
        r = MockResource()
        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
            r.plot_canvas()

    def test_instance_can_override_class_callback(self, monkeypatch) -> None:
        class_calls: list[str] = []
        monkeypatch.setattr(BaseResource, "_plot_canvas", lambda: class_calls.append("class"))
        r = MockResource()
        instance_calls: list[str] = []
        r._plot_canvas = lambda: instance_calls.append("instance")
        r.plot_canvas()
        assert class_calls == []
        assert instance_calls == ["instance"]


class TestRegisterCanvasCallbacks:
    """Class-wide registration of buffer and composite callbacks."""

    def test_register_update_canvas_sets_class_attribute(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_update_canvas", None)

        def cb(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
        ) -> None:
            return None

        BaseResource.register_update_canvas(cb)
        assert BaseResource._update_canvas is cb

    def test_register_plot_canvas_sets_class_attribute(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_plot_canvas", None)

        def cb() -> None:
            return None

        BaseResource.register_plot_canvas(cb)
        assert BaseResource._plot_canvas is cb
