"""Tests for base_resource.py — BaseResource abstract class."""

from pathlib import Path

import pytest
from PIL import Image

from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.task import ApplySceneTask
from wallpaper_auto.util.display_util import LUID, DisplayId, DisplayInfo
from wallpaper_auto.util.wallpaper_util import WallpaperStyle


def _noop_canvas(
    display_id: DisplayId,
    style: WallpaperStyle,
    image: Path | Image.Image,
) -> None:
    """Canvas stub that does nothing — matches UpdateCanvasProtocol exactly."""


def _make_display() -> DisplayInfo:
    """DisplayInfo with a stable identity for resource binding."""
    return DisplayInfo(
        device_name="\\\\.\\DISPLAY1",
        model="U2719D",
        source_resolution=(1920, 1080),
        position=(0, 0),
        target_resolution=(1920, 1080),
        adapter_id=LUID(1, 2),
        source_id=3,
        scale=100,
        monitor_device_path=r"\\?\DISPLAY#TEST#1",
    )


class MockResource(BaseResource):
    """Concrete BaseResource subclass for testing abstract methods."""

    def mount(self) -> None:
        pass

    def demount(self) -> None:
        pass


class TestMockResource:
    def test_mount_exists(self) -> None:
        MockResource().mount()

    def test_demount_exists(self) -> None:
        MockResource().demount()

    def test_can_instantiate(self) -> None:
        MockResource()


class TestInitState:
    def test_display_is_none(self) -> None:
        assert MockResource().display is None

    def test_update_canvas_is_none(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_update_canvas", None)
        assert MockResource()._update_canvas is None

    def test_plot_canvas_is_none(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_plot_canvas", None)
        assert MockResource()._plot_canvas is None


class TestBindDisplay:
    def test_bind_sets_display(self) -> None:
        r = MockResource()
        r._bind_display(_make_display())
        assert r.display is not None
        assert r.display.model == "U2719D"


class TestUpdateCanvas:
    def test_dispatches_to_callback(self) -> None:
        r = MockResource()
        display = _make_display()
        r._bind_display(display)
        captured: list[tuple[object, ...]] = []

        def canvas(
            display_id: DisplayId,
            style: WallpaperStyle,
            image: Path | Image.Image,
        ) -> None:
            captured.append((display_id, style, image))

        r._update_canvas = canvas
        img = Image.new("RGB", (4, 4))
        r.update_canvas(WallpaperStyle.FILL, img)

        assert captured == [(display.display_id, WallpaperStyle.FILL, img)]

    def test_accepts_path_image(self) -> None:
        r = MockResource()
        r._bind_display(_make_display())
        captured: list[object] = []

        def canvas(
            display_id: DisplayId,
            style: WallpaperStyle,
            image: Path | Image.Image,
        ) -> None:
            captured.append(image)

        r._update_canvas = canvas
        path = Path("/tmp/wallpaper.png")
        r.update_canvas(WallpaperStyle.STRETCH, path)
        assert captured == [path]

    def test_propagates_canvas_exception(self) -> None:
        r = MockResource()
        r._bind_display(_make_display())

        def canvas(
            display_id: DisplayId,
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
        r._bind_display(_make_display())
        with pytest.raises(RuntimeError, match="update_canvas not bound"):
            r.update_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))

    def test_raises_when_display_unbound(self) -> None:
        r = MockResource()
        r._update_canvas = _noop_canvas
        with pytest.raises(RuntimeError, match="display not bound"):
            r.update_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))


class TestPlotCanvas:
    def test_dispatches_to_class_callback(self, monkeypatch) -> None:
        called: list[bool] = []
        # staticmethod wrapper — a plain function stored on the class would be
        # bound as a method on instance access.
        monkeypatch.setattr(
            BaseResource,
            "_plot_canvas",
            staticmethod(lambda: (called.append(True), ApplySceneTask())[1]),
        )
        MockResource().plot_canvas()
        assert called == [True]

    def test_raises_when_callback_unbound(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_plot_canvas", None)
        r = MockResource()
        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
            r.plot_canvas()

    def test_instance_can_override_class_callback(self, monkeypatch) -> None:
        class_calls: list[str] = []
        monkeypatch.setattr(
            BaseResource,
            "_plot_canvas",
            lambda: (class_calls.append("class"), ApplySceneTask())[1],
        )
        r = MockResource()
        instance_calls: list[str] = []
        r._plot_canvas = lambda: (instance_calls.append("instance"), ApplySceneTask())[1]
        r.plot_canvas()
        assert class_calls == []
        assert instance_calls == ["instance"]


class TestRegisterCanvasCallbacks:
    def test_register_update_canvas_sets_class_attribute(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_update_canvas", None)

        def cb(
            display_id: DisplayId,
            style: WallpaperStyle,
            image: Path | Image.Image,
        ) -> None:
            return None

        BaseResource.register_update_canvas(cb)
        assert BaseResource._update_canvas is cb

    def test_register_plot_canvas_sets_class_attribute(self, monkeypatch) -> None:
        monkeypatch.setattr(BaseResource, "_plot_canvas", None)

        def cb() -> ApplySceneTask:
            return ApplySceneTask()

        BaseResource.register_plot_canvas(cb)
        assert BaseResource._plot_canvas is cb
