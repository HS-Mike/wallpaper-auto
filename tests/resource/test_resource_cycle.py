"""
Tests for resource_cycle.py — ResourceCycle.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource.resource_cycle import ResourceCycle
from wallpaper_auto.util.display_util import LUID, DisplayInfo

_DEVICE_PATH = r"\\?\DISPLAY#TEST#{test-device}"


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
        monitor_device_path=_DEVICE_PATH,
    )


@pytest.fixture
def mock_sub_resources():
    """Create 3 mock BaseResource instances for resource cycle testing."""
    return [MagicMock(spec=BaseResource) for _ in range(3)]


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

    def test_invalid_resource_type_raises(self):
        """Non-BaseResource, non-dict entries raise TypeError."""
        with pytest.raises(TypeError, match="Expected BaseResource or dict"):
            ResourceCycle(resources=["invalid_string"])  # type: ignore[arg-type]

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

        with patch.dict(
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

    def test_dict_resource_unknown_type_raises(self):
        """Unknown resource type in dict raises ValueError."""
        with pytest.raises(ValueError, match="Unknown resource type"):
            ResourceCycle(resources=[{"name": "nonexistent", "config": {}}])


class TestResourceCycleLifecycle:
    """Mount lifecycle."""

    def test_mount_demount_lifecycle(self, mock_sub_resources):
        """mount starts background thread, mounts a sub-resource, demount stops it."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        cycle._bind_display(_make_display())
        cycle._plot_canvas = MagicMock()
        cycle.mount()
        assert cycle._cycling_thread is not None
        assert cycle._cycling_thread.is_alive()

        # mount() advances past index 0, so resources[1] is mounted synchronously
        mock_sub_resources[1].mount.assert_called_once()

        cycle.demount()
        assert cycle._cycling_thread is None

    def test_mount_random_starts_at_random_index(self, mock_sub_resources):
        """With random=True, a different starting index may be selected."""
        cycle = ResourceCycle(resources=mock_sub_resources, random=True)
        cycle._bind_display(_make_display())
        cycle._plot_canvas = MagicMock()
        cycle.mount()

        deadline = time.monotonic() + 5.0
        while (
            all(r.mount.call_count == 0 for r in mock_sub_resources) and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        called_count = sum(r.mount.called for r in mock_sub_resources)
        assert called_count >= 1
        assert 0 <= cycle._index < 3
        cycle.demount()

    def test_mount_raises_when_device_not_bound(self, mock_sub_resources):
        """mount raises if the display is not bound."""
        cycle = ResourceCycle(resources=mock_sub_resources)
        with pytest.raises(AssertionError):
            cycle.mount()


class TestResourceCycleCycling:
    """Index advancement and cycling thread behavior."""

    def test_advance_index_sequential(self, mock_sub_resources):
        """_advance_index cycles 0→1→2→0 (wraps around with 3 resources)."""
        cycle = ResourceCycle(resources=mock_sub_resources, random=False)
        assert cycle._index == 0

        cycle._advance_index()
        assert cycle._index == 1

        cycle._advance_index()
        assert cycle._index == 2

        # wraps from last index back to 0
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
        cycle._bind_display(_make_display())
        cycle._plot_canvas = MagicMock()
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
        cycle._bind_display(_make_display())
        cycle._plot_canvas = MagicMock()
        cycle.mount()
        assert cycle._cycling_thread is not None and cycle._cycling_thread.is_alive()

        cycle._stop_event.set()
        cycle._cycling_thread.join(timeout=1.0)
        assert not cycle._cycling_thread.is_alive()

        cycle.demount()


class TestResourceCycleEdgeCases:
    """Edge cases and boundary conditions."""

    def test_non_positive_interval_raises(self, mock_sub_resources):
        """interval <= 0 raises ValueError."""
        with pytest.raises(ValueError, match="interval must be a positive"):
            ResourceCycle(resources=mock_sub_resources, interval=0)
        with pytest.raises(ValueError, match="interval must be a positive"):
            ResourceCycle(resources=mock_sub_resources, interval=-1)

    def test_single_resource_no_cycling(self, mock_sub_resources):
        """Single resource — cycling thread runs but _advance_index loops on same index."""
        single = [mock_sub_resources[0]]
        cycle = ResourceCycle(resources=single, interval=0.05)
        cycle._bind_display(_make_display())
        cycle._plot_canvas = MagicMock()
        cycle.mount()
        assert cycle._index == 0

        deadline = time.monotonic() + 1.0
        while mock_sub_resources[0].demount.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.02)

        cycle.demount()

        assert mock_sub_resources[0].demount.call_count >= 2
        assert mock_sub_resources[0].mount.call_count >= 3
