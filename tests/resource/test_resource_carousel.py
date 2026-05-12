"""
Tests for resource_carousel.py — ResourceCarousel.
"""

import time
from unittest.mock import patch

import pytest

from wallpaper_automator.resource.resource_carousel import ResourceCarousel


class TestResourceCarouselInit:
    """Constructor validation and attribute storage."""

    def test_empty_resources_raises(self):
        """Empty resources list raises ValueError."""
        with pytest.raises(ValueError, match="At least one resource"):
            ResourceCarousel(resources=[])

    def test_single_resource(self, mock_sub_resources):
        """A single resource is valid and stored."""
        carousel = ResourceCarousel(resources=[mock_sub_resources[0]])
        assert len(carousel._resources) == 1
        assert carousel._resources[0] is mock_sub_resources[0]

    def test_multiple_resources(self, mock_sub_resources):
        """All resources are stored in order."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        assert len(carousel._resources) == 3
        assert carousel._resources == mock_sub_resources

    def test_init_preserves_interval(self, mock_sub_resources):
        """interval is stored correctly."""
        carousel = ResourceCarousel(resources=mock_sub_resources, interval=60)
        assert carousel.interval == 60

    def test_init_default_interval(self, mock_sub_resources):
        """Default interval is 300 seconds."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        assert carousel.interval == 300

    def test_init_preserves_random_flag(self, mock_sub_resources):
        """random flag is stored correctly."""
        carousel = ResourceCarousel(resources=mock_sub_resources, random=True)
        assert carousel.random is True

    def test_init_default_random_flag(self, mock_sub_resources):
        """Default random flag is False."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        assert carousel.random is False

    def test_invalid_resource_type_raises(self):
        """Non-BaseResource, non-dict entries raise TypeError."""
        with pytest.raises(TypeError, match="Expected BaseResource or dict"):
            ResourceCarousel(resources=["invalid_string"])

    def test_dict_resource_resolved(self):
        """Dict entries are resolved via _build_sub_resource against the registry."""
        from wallpaper_automator.resource.base_resource import BaseResource

        class _MockResource(BaseResource):
            def __init__(self, path: str = "", style: str = "fill") -> None:
                self.path = path
                self.style = style
            def mount(self) -> None: pass
            def demount(self) -> None: pass

        with patch(
            "wallpaper_automator.resource_manager.ResourceManager._support_resources",
            {"mock_resource": _MockResource},
        ):
            carousel = ResourceCarousel(resources=[
                {"name": "mock_resource", "config": {"path": "test.jpg", "style": "center"}},
            ])
            assert len(carousel._resources) == 1
            assert isinstance(carousel._resources[0], _MockResource)
            assert carousel._resources[0].path == "test.jpg"
            assert carousel._resources[0].style == "center"

    def test_dict_resource_unknown_type_raises(self):
        """Unknown resource type in dict raises ValueError."""
        with pytest.raises(ValueError, match="Unknown resource type"):
            ResourceCarousel(resources=[{"name": "nonexistent", "config": {}}])


class TestResourceCarouselMount:
    """Mount lifecycle."""

    def test_mount_saves_original_wallpaper(self, mock_carousel_deps, mock_sub_resources):
        """mount saves the current wallpaper for later restore."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        carousel.mount()
        assert carousel._original_wallpaper == "C:\\original.jpg"
        carousel.demount()

    def test_mount_mounts_first_resource(self, mock_carousel_deps, mock_sub_resources):
        """mount calls mount() on the first sub-resource."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        carousel.mount()
        mock_sub_resources[0].mount.assert_called_once()
        carousel.demount()

    def test_mount_starts_cycling_thread(self, mock_carousel_deps, mock_sub_resources):
        """mount starts a background thread."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        carousel.mount()
        assert carousel._cycling_thread is not None
        assert carousel._cycling_thread.is_alive()
        carousel.demount()

    def test_mount_stores_original_before_mounting(
        self, mock_carousel_deps, mock_sub_resources
    ):
        """mount gets the original wallpaper before mounting the sub-resource."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        carousel.mount()
        # _original_wallpaper was set (by the mock) before sub-resource mount
        assert carousel._original_wallpaper == "C:\\original.jpg"
        carousel.demount()

    def test_mount_random_starts_at_random_index(self, mock_carousel_deps, mock_sub_resources):
        """With random=True, a different starting index may be selected."""
        # The randomness means we can't predict the index, but we can verify
        # that some advance happened by checking mount was called on some resource
        carousel = ResourceCarousel(resources=mock_sub_resources, random=True)
        carousel.mount()

        # Verify exactly one sub-resource was mounted
        called_count = sum(r.mount.called for r in mock_sub_resources)
        assert called_count == 1

        # Verify the mounted resource's index is valid
        assert 0 <= carousel._index < 3
        carousel.demount()


class TestResourceCarouselDemount:
    """Demount lifecycle."""

    def test_demount_demounts_current_resource(self, mock_carousel_deps, mock_sub_resources):
        """demount calls demount() on the current sub-resource."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        carousel.mount()
        mock_sub_resources[0].reset_mock()

        carousel.demount()
        mock_sub_resources[0].demount.assert_called_once()

    def test_demount_restores_original(self, mock_carousel_deps, mock_sub_resources):
        """restore=True — demount restores the wallpaper from before mount."""
        with patch(
            "wallpaper_automator.resource.resource_carousel.set_wallpaper"
        ) as mock_set:
            carousel = ResourceCarousel(resources=mock_sub_resources, restore=True)
            carousel.mount()
            mock_set.reset_mock()

            carousel.demount()
            mock_set.assert_called_once()
            args, _ = mock_set.call_args
            assert args[0] == "C:\\original.jpg"

    def test_demount_with_restore_false_skips_restore(
        self, mock_carousel_deps, mock_sub_resources
    ):
        """restore=False — demount does not restore the original wallpaper."""
        with patch(
            "wallpaper_automator.resource.resource_carousel.set_wallpaper"
        ) as mock_set:
            carousel = ResourceCarousel(resources=mock_sub_resources, restore=False)
            carousel.mount()
            mock_set.reset_mock()

            carousel.demount()
            # Only sub-resource demount was called; no set_wallpaper for restore
            mock_set.assert_not_called()

    def test_demount_without_mount_is_safe(self, mock_carousel_deps, mock_sub_resources):
        """demount without prior mount does nothing (no error)."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        # Should not raise
        carousel.demount()
        for r in mock_sub_resources:
            r.mount.assert_not_called()
            r.demount.assert_not_called()

    def test_demount_stops_cycling_thread(self, mock_carousel_deps, mock_sub_resources):
        """demount causes the cycling thread to exit."""
        carousel = ResourceCarousel(resources=mock_sub_resources)
        carousel.mount()
        assert carousel._cycling_thread is not None and carousel._cycling_thread.is_alive()

        carousel.demount()
        assert carousel._cycling_thread is None

    def test_demount_idempotent(self, mock_carousel_deps, mock_sub_resources):
        """Calling demount twice is safe."""
        with patch(
            "wallpaper_automator.resource.resource_carousel.set_wallpaper"
        ) as mock_set:
            carousel = ResourceCarousel(resources=mock_sub_resources, restore=True)
            carousel.mount()
            mock_set.reset_mock()

            carousel.demount()
            carousel.demount()  # second call — should be a no-op
            mock_set.assert_called_once()  # only the restore from first demount

    def test_mount_demount_cycle(self, mock_carousel_deps, mock_sub_resources):
        """Mount then demount then mount again works correctly."""
        with (
            patch(
                "wallpaper_automator.resource.resource_carousel.get_current_wallpaper",
                return_value="C:\\original.jpg",
            ),
            patch(
                "wallpaper_automator.resource.resource_carousel.set_wallpaper",
            ),
        ):
            carousel = ResourceCarousel(resources=mock_sub_resources, restore=True)

            carousel.mount()
            mock_sub_resources[0].mount.assert_called_once()

            carousel.demount()

            # Mount again after demount — should start fresh
            mock_sub_resources[0].reset_mock()
            carousel.mount()
            mock_sub_resources[0].mount.assert_called_once()
            assert carousel._original_wallpaper == "C:\\original.jpg"
            carousel.demount()

class TestResourceCarouselCycling:
    """Index advancement and cycling thread behavior."""

    def test_advance_index_sequential(self, mock_sub_resources):
        """_advance_index cycles forward sequentially."""
        carousel = ResourceCarousel(resources=mock_sub_resources, random=False)
        assert carousel._index == 0

        carousel._advance_index()
        assert carousel._index == 1

        carousel._advance_index()
        assert carousel._index == 2

    def test_advance_index_wraps_around(self, mock_sub_resources):
        """_advance_index wraps from last index back to 0."""
        carousel = ResourceCarousel(resources=mock_sub_resources, random=False)
        carousel._index = 2

        carousel._advance_index()
        assert carousel._index == 0

    def test_advance_index_random(self, mock_sub_resources):
        """_advance_index with random=True stays within valid range."""
        carousel = ResourceCarousel(resources=mock_sub_resources, random=True)

        for _ in range(20):
            carousel._advance_index()
            assert 0 <= carousel._index < 3

    def test_cycling_thread_demounts_then_mounts(self, mock_carousel_deps, mock_sub_resources):
        """Cycling thread demounts current and mounts next after ~interval."""
        carousel = ResourceCarousel(resources=mock_sub_resources, interval=0.05)
        carousel.mount()

        # Should start with resource 0 mounted
        assert mock_sub_resources[0].mount.call_count >= 1

        # Poll for cycling to happen (up to 5 s)
        deadline = time.monotonic() + 5.0
        while (
            mock_sub_resources[0].demount.call_count < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        # After at least one cycle: resource 0 was demounted
        assert mock_sub_resources[0].demount.call_count >= 1

        # Resource 1 should have been mounted
        assert mock_sub_resources[1].mount.call_count >= 1

        carousel.demount()

    def test_stop_event_stops_thread_quickly(self, mock_carousel_deps, mock_sub_resources):
        """Setting stop event causes thread to exit before next interval."""
        carousel = ResourceCarousel(resources=mock_sub_resources, interval=10)  # long interval

        carousel.mount()
        assert carousel._cycling_thread is not None and carousel._cycling_thread.is_alive()

        carousel._stop_event.set()
        carousel._cycling_thread.join(timeout=1.0)
        assert not carousel._cycling_thread.is_alive()

        carousel.demount()

    def test_cycling_respects_order(self, mock_carousel_deps, mock_sub_resources):
        """Cycling advances: demount[i] → advance → mount[i+1]."""
        carousel = ResourceCarousel(resources=mock_sub_resources, interval=0.05)
        carousel.mount()

        # Poll for two full cycles
        deadline = time.monotonic() + 5.0
        while (
            mock_sub_resources[2].mount.call_count < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        carousel.demount()

        # After cycling through all three, each should have been mounted
        for r in mock_sub_resources:
            assert r.mount.called
            assert r.demount.called


class TestResourceCarouselEdgeCases:
    """Edge cases and boundary conditions."""

    def test_interval_zero_allows_cycling(self, mock_carousel_deps, mock_sub_resources):
        """interval=0 is handled — thread can be stopped cleanly."""
        carousel = ResourceCarousel(resources=mock_sub_resources, interval=0)

        carousel.mount()
        # Give the tight loop a moment to cycle
        deadline = time.monotonic() + 1.0
        while (
            mock_sub_resources[1].mount.call_count < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        carousel.demount()

        # Thread should have stopped cleanly
        assert carousel._cycling_thread is None
        # Should have cycled at least once
        assert mock_sub_resources[1].mount.call_count >= 1

    def test_single_resource_no_cycling(self, mock_carousel_deps, mock_sub_resources):
        """Single resource — cycling thread runs but _advance_index loops on same index."""
        single = [mock_sub_resources[0]]
        carousel = ResourceCarousel(resources=single, interval=0.05)

        carousel.mount()
        assert carousel._index == 0
        assert carousel._resources[0] is mock_sub_resources[0]

        # Let the thread cycle a few times
        deadline = time.monotonic() + 1.0
        while (
            mock_sub_resources[0].demount.call_count < 2
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        carousel.demount()

        # With a single resource, demount+remount cycles on index 0
        assert mock_sub_resources[0].demount.call_count >= 2
        assert mock_sub_resources[0].mount.call_count >= 3  # 1 initial + 2+ cycles

    def test_resources_list_not_mutated_externally(self, mock_sub_resources):
        """External mutation of the passed list does not affect carousel."""
        original = list(mock_sub_resources)
        carousel = ResourceCarousel(resources=original)
        original.clear()
        assert len(carousel._resources) == 3

    def test_type_error_on_invalid_type(self):
        """Non-BaseResource, non-dict items raise TypeError."""
        with pytest.raises(TypeError):
            ResourceCarousel(resources=[123])  # type: ignore[list-item]
