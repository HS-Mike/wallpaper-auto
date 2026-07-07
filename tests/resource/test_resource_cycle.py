"""
Tests for resource_cycle.py — ResourceCycle.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.resource.resource_cycle import ResourceCycle

_DEVICE_PATH = r"\\?\DISPLAY#TEST#{test-device}"


class TestResourceCycleInit:
    """Constructor validation and attribute storage."""

    def test_empty_resources_raises(self):
        """Empty resources list raises ValueError."""
        with pytest.raises(ValueError, match="At least one resource"):
            ResourceCycle(resources=[])

    def test_single_resource(self, mock_sub_resources):
        """A single resource is valid and stored."""
        cycle = ResourceCycle(resources=[mock_sub_resources[0]])
        assert len(cycle._resources) == 1
        assert cycle._resources[0] is mock_sub_resources[0]

    def test_multiple_resources(self, mock_sub_resources):
        """All resources are stored in order."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        assert len(cycle._resources) == 3
        assert cycle._resources == mock_sub_resources

    def test_init_preserves_interval(self, mock_sub_resources):
        """interval is stored correctly."""
        cycle = ResourceCycle(resources=mock_sub_resources, interval=60)
        assert cycle.interval == 60

    def test_init_default_interval(self, mock_sub_resources):
        """Default interval is 300 seconds."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        assert cycle.interval == 300

    def test_init_preserves_random_flag(self, mock_sub_resources):
        """random flag is stored correctly."""
        cycle = ResourceCycle(resources=mock_sub_resources, random=True)
        assert cycle.random is True

    def test_init_default_random_flag(self, mock_sub_resources):
        """Default random flag is False."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        assert cycle.random is False

    def test_invalid_resource_type_raises(self):
        """Non-BaseResource, non-dict entries raise TypeError."""
        with pytest.raises(TypeError, match="Expected BaseResource or dict"):
            ResourceCycle(resources=["invalid_string"])

    def test_dict_resource_resolved(self):
        """Dict entries are resolved via _build_sub_resource against the registry."""
        from wallpaper_auto.resource.base_resource import BaseResource

        class _MockResource(BaseResource):
            def __init__(self, path: str = "", style: str = "fill") -> None:
                self.path = path
                self.style = style

            def mount(self) -> None:
                pass

            def demount(self) -> None:
                pass

        with patch(
            "wallpaper_auto.resource_manager.ResourceManager._support_resources",
            {"mock_resource": _MockResource},
        ):
            cycle = ResourceCycle(
                resources=[
                    {"name": "mock_resource", "config": {"path": "test.jpg", "style": "center"}},
                ]
            )
            assert len(cycle._resources) == 1
            assert isinstance(cycle._resources[0], _MockResource)
            assert cycle._resources[0].path == "test.jpg"
            assert cycle._resources[0].style == "center"

    def test_dict_resource_unknown_type_raises(self):
        """Unknown resource type in dict raises ValueError."""
        with pytest.raises(ValueError, match="Unknown resource type"):
            ResourceCycle(resources=[{"name": "nonexistent", "config": {}}])


class TestResourceCycleMount:
    """Mount lifecycle."""

    def test_mount_starts_cycling_thread(self, mock_sub_resources):
        """mount starts a background thread."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()
        assert cycle._cycling_thread is not None
        assert cycle._cycling_thread.is_alive()
        cycle.demount()

    def test_mount_mounts_first_resource(self, mock_sub_resources):
        """mount triggers mount() on the first sub-resource via the cycling thread."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()

        deadline = time.monotonic() + 5.0
        while mock_sub_resources[0].mount.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.02)

        assert mock_sub_resources[0].mount.call_count >= 1
        cycle.demount()

    def test_mount_random_starts_at_random_index(self, mock_sub_resources):
        """With random=True, a different starting index may be selected."""
        cycle = ResourceCycle(resources=mock_sub_resources, random=True)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()

        deadline = time.monotonic() + 5.0
        while all(r.mount.call_count == 0 for r in mock_sub_resources) and time.monotonic() < deadline:
            time.sleep(0.02)

        called_count = sum(r.mount.called for r in mock_sub_resources)
        assert called_count >= 1
        assert 0 <= cycle._index < 3
        cycle.demount()

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_mount_raises_when_device_not_bound(self, mock_sub_resources):
        """mount starts thread that fails if monitor_device_path is not bound."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle.mount()
        # Thread will crash due to missing bindings — give it time to start
        cycle._cycling_thread.join(timeout=5.0)
        assert not cycle._cycling_thread.is_alive()


class TestResourceCycleDemount:
    """Demount lifecycle."""

    def test_demount_demounts_current_resource(self, mock_sub_resources):
        """demount causes the cycling thread to exit; sub-resource may have been mounted."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()

        deadline = time.monotonic() + 5.0
        while mock_sub_resources[0].mount.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.02)

        cycle.demount()
        assert cycle._cycling_thread is None

    def test_demount_without_mount_is_safe(self, mock_sub_resources):
        """demount without prior mount does nothing (no error)."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle.demount()

    def test_demount_stops_cycling_thread(self, mock_sub_resources):
        """demount causes the cycling thread to exit."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()
        assert cycle._cycling_thread is not None and cycle._cycling_thread.is_alive()

        cycle.demount()
        assert cycle._cycling_thread is None

    def test_demount_idempotent(self, mock_sub_resources):
        """Calling demount twice is safe."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()

        deadline = time.monotonic() + 5.0
        while mock_sub_resources[0].mount.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.02)

        cycle.demount()
        cycle.demount()  # second call — should be a no-op

    def test_mount_demount_cycle(self, mock_sub_resources):
        """Mount then demount then mount again works correctly."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())

        cycle.mount()
        deadline = time.monotonic() + 5.0
        while mock_sub_resources[0].mount.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        cycle.demount()

        # Mount again after demount — should start fresh
        cycle.mount()
        deadline = time.monotonic() + 5.0
        while mock_sub_resources[0].mount.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert mock_sub_resources[0].mount.call_count >= 2
        cycle.demount()


class TestResourceCycleCycling:
    """Index advancement and cycling thread behavior."""

    def test_advance_index_sequential(self, mock_sub_resources):
        """_advance_index cycles forward sequentially."""
        cycle = ResourceCycle(resources=mock_sub_resources, random=False)
        assert cycle._index == 0

        cycle._advance_index()
        assert cycle._index == 1

        cycle._advance_index()
        assert cycle._index == 2

    def test_advance_index_wraps_around(self, mock_sub_resources):
        """_advance_index wraps from last index back to 0."""
        cycle = ResourceCycle(resources=mock_sub_resources, random=False)
        cycle._index = 2

        cycle._advance_index()
        assert cycle._index == 0

    def test_advance_index_random(self, mock_sub_resources):
        """_advance_index with random=True stays within valid range."""
        cycle = ResourceCycle(resources=mock_sub_resources, random=True)

        for _ in range(20):
            cycle._advance_index()
            assert 0 <= cycle._index < 3

    def test_cycling_thread_demounts_then_mounts(self, mock_sub_resources):
        """Cycling thread demounts current and mounts next after ~interval."""
        cycle = ResourceCycle(resources=mock_sub_resources, interval=0.05)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()

        deadline = time.monotonic() + 5.0
        while mock_sub_resources[0].demount.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.02)

        assert mock_sub_resources[0].demount.call_count >= 1
        assert mock_sub_resources[1].mount.call_count >= 1

        cycle.demount()

    def test_stop_event_stops_thread_quickly(self, mock_sub_resources):
        """Setting stop event causes thread to exit before next interval."""
        cycle = ResourceCycle(resources=mock_sub_resources, interval=10)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()
        assert cycle._cycling_thread is not None and cycle._cycling_thread.is_alive()

        cycle._stop_event.set()
        cycle._cycling_thread.join(timeout=1.0)
        assert not cycle._cycling_thread.is_alive()

        cycle.demount()

    def test_cycling_respects_order(self, mock_sub_resources):
        """Cycling advances: demount[i] → advance → mount[i+1]."""
        cycle = ResourceCycle(resources=mock_sub_resources, interval=0.05)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()

        deadline = time.monotonic() + 5.0
        while mock_sub_resources[2].mount.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.02)

        cycle.demount()

        for r in mock_sub_resources:
            assert r.mount.called
            assert r.demount.called


class TestResourceCycleEdgeCases:
    """Edge cases and boundary conditions."""

    def test_interval_zero_allows_cycling(self, mock_sub_resources):
        """interval=0 is handled — thread can be stopped cleanly."""
        cycle = ResourceCycle(resources=mock_sub_resources, interval=0)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()

        deadline = time.monotonic() + 1.0
        while mock_sub_resources[1].mount.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        cycle.demount()

        assert cycle._cycling_thread is None
        assert mock_sub_resources[1].mount.call_count >= 1

    def test_single_resource_no_cycling(self, mock_sub_resources):
        """Single resource — cycling thread runs but _advance_index loops on same index."""
        single = [mock_sub_resources[0]]
        cycle = ResourceCycle(resources=single, interval=0.05)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        cycle._bind_plot_canvas(MagicMock())
        cycle.mount()
        assert cycle._index == 0

        deadline = time.monotonic() + 1.0
        while mock_sub_resources[0].demount.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.02)

        cycle.demount()

        assert mock_sub_resources[0].demount.call_count >= 2
        assert mock_sub_resources[0].mount.call_count >= 3

    def test_resources_list_not_mutated_externally(self, mock_sub_resources):
        """External mutation of the passed list does not affect cycle."""
        original = list(mock_sub_resources)
        cycle = ResourceCycle(resources=original)
        original.clear()
        assert len(cycle._resources) == 3

    def test_type_error_on_invalid_type(self):
        """Non-BaseResource, non-dict items raise TypeError."""
        with pytest.raises(TypeError):
            ResourceCycle(resources=[123])  # type: ignore[list-item]


class TestGetPlotCanvasWrapper:
    """The plot_canvas_wrapper returned by get_plot_canvas_wrapper."""

    def test_wrapper_calls_plot_canvas_with_immediate_update(
        self, mock_sub_resources
    ):
        """Wrapper forces immediate_update=True regardless of caller arg."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        canvas_mock = MagicMock()
        cycle._bind_plot_canvas(canvas_mock)
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        wrapper = cycle.get_plot_canvas_wrapper()

        wrapper(_DEVICE_PATH, "style_dummy", "img_dummy", immediate_update=False)

        canvas_mock.assert_called_once_with(_DEVICE_PATH, "style_dummy", "img_dummy", True)

    def test_wrapper_asserts_plot_canvas_bound(self, mock_sub_resources):
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle._bind_plot_canvas(MagicMock())
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        wrapper = cycle.get_plot_canvas_wrapper()
        # Simulate the canvas being unbound after the wrapper is captured.
        cycle._plot_canvas = None
        with pytest.raises(AssertionError, match="plot_canvas not bound"):
            wrapper(_DEVICE_PATH, "style", "img")

    def test_wrapper_asserts_monitor_path_bound(self, mock_sub_resources):
        cycle = ResourceCycle(resources=mock_sub_resources)
        # Bind both, capture the wrapper, then unbind the path.
        cycle._bind_plot_canvas(MagicMock())
        cycle._bind_monitor_device_path(_DEVICE_PATH)
        wrapper = cycle.get_plot_canvas_wrapper()
        cycle.monitor_device_path = None
        with pytest.raises(AssertionError, match="monitor_device_path not bound"):
            wrapper(_DEVICE_PATH, "style", "img")
