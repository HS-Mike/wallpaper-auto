"""Tests for resource_manager.py — ResourceManager static API."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from wallpaper_auto.models import ResourceConfig, SceneBinding, SceneConfig
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource_manager import (
    _BUILTIN_RESOURCES,
    DisplayScene,
    ResourceManager,
    register_transition,
)
from wallpaper_auto.util.display_util import LUID, DisplayInfo


def _make_display_info(
    device_path: str,
    model: str | None = "Monitor",
    x: int = 0,
) -> DisplayInfo:
    """Create a DisplayInfo with sensible defaults for testing."""
    return DisplayInfo(
        device_name="\\\\.\\DISPLAY1",
        monitor_device_path=device_path,
        model=model,
        source_resolution=(1920, 1080),
        position=(x, 0),
        target_resolution=(1920, 1080),
        adapter_id=LUID(1, 2),
        source_id=3,
        scale=100,
    )


def _make_resource_class(name: str) -> type[BaseResource]:
    """Create a concrete BaseResource subclass for exact-type transition tests."""
    return type(name, (BaseResource,), {"mount": lambda s: None, "demount": lambda s: None})


def _noop_transition(old: BaseResource, new: BaseResource) -> None:
    """Transition that does nothing."""


def _make_method_resource_class(
    name: str, seen: list[tuple[BaseResource, BaseResource]]
) -> type[BaseResource]:
    """A BaseResource subclass whose ``on_handoff`` records ``(self, new)``."""
    return type(
        name,
        (BaseResource,),
        {
            "mount": lambda s: None,
            "demount": lambda s: None,
            "on_handoff": lambda self, new: seen.append((self, new)),
        },
    )


@pytest.fixture
def _clean_transitions() -> Iterator[None]:
    """Isolate the class-wide transition registry for the duration of a test."""
    with patch.dict(ResourceManager._resource_transitions, clear=True):
        yield


class TestResourceManagerInit:
    """ResourceManager class-level defaults."""

    def test_builtin_resources_are_registered(self):
        builtin_keys = set(_BUILTIN_RESOURCES.keys())
        assert ResourceManager._support_resources.keys() >= builtin_keys

    def test_support_resources_are_a_copy_of_builtin(self):
        assert ResourceManager._support_resources is not _BUILTIN_RESOURCES


class TestResourceManagerRegisterResource:
    """ResourceManager.register_resource class method."""

    def test_register_custom_subclass_succeeds(self):
        cls = type(
            "CustomResource",
            (BaseResource,),
            {"mount": lambda s: None, "demount": lambda s: None},
        )
        with patch.dict(ResourceManager._support_resources, clear=False):
            ResourceManager.register_resource("test", cls)
            assert ResourceManager._support_resources["test"] is cls

    def test_register_non_subclass_raises(self):
        with patch.dict(ResourceManager._support_resources, clear=False):
            with pytest.raises(ValueError, match="resource cls must inherit from BaseResource"):
                ResourceManager.register_resource("bad", object)  # type: ignore


@pytest.mark.usefixtures("_clean_transitions")
class TestResourceManagerRegisterTransition:
    """ResourceManager.register_transition class method."""

    def test_registered_transition_is_stored_under_the_exact_class_pair(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")

        ResourceManager.register_transition(old_cls, new_cls, _noop_transition)

        assert ResourceManager._resource_transitions[(old_cls, new_cls)] == [_noop_transition]

    def test_non_subclass_old_class_raises(self):
        with pytest.raises(ValueError, match="old_cls must inherit from BaseResource"):
            ResourceManager.register_transition(
                object,  # type: ignore
                _make_resource_class("NewRes"),
                _noop_transition,
            )

    def test_non_subclass_new_class_raises(self):
        with pytest.raises(ValueError, match="new_cls must inherit from BaseResource"):
            ResourceManager.register_transition(
                _make_resource_class("OldRes"),
                object,  # type: ignore
                _noop_transition,
            )

    def test_duplicate_pair_appends_in_registration_order(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")

        def first(old: BaseResource, new: BaseResource) -> None:
            return None

        def second(old: BaseResource, new: BaseResource) -> None:
            return None

        ResourceManager.register_transition(old_cls, new_cls, first)
        ResourceManager.register_transition(old_cls, new_cls, second)

        assert ResourceManager._resource_transitions[(old_cls, new_cls)] == [first, second]

    def test_direct_form_returns_none(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")

        assert ResourceManager.register_transition(old_cls, new_cls, _noop_transition) is None

    def test_module_level_alias_registers_on_the_same_registry(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")

        register_transition(old_cls, new_cls, _noop_transition)

        assert ResourceManager._resource_transitions[(old_cls, new_cls)] == [_noop_transition]

    def test_direct_form_accepts_a_method_of_the_outgoing_class(self):
        seen: list[tuple[BaseResource, BaseResource]] = []
        old_cls = _make_method_resource_class("OldRes", seen)
        new_cls = _make_resource_class("NewRes")

        ResourceManager.register_transition(old_cls, new_cls, getattr(old_cls, "on_handoff"))

        old = old_cls()
        new = new_cls()
        ResourceManager.run_resource_transitions(old, new)

        assert seen == [(old, new)]


@pytest.mark.usefixtures("_clean_transitions")
class TestResourceManagerRegisterTransitionDecorator:
    """Decorator form of ResourceManager.register_transition."""

    def test_decorator_registers_function_and_returns_it_unchanged(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")

        decorator = ResourceManager.register_transition(old_cls, new_cls)

        assert decorator(_noop_transition) is _noop_transition
        assert ResourceManager._resource_transitions[(old_cls, new_cls)] == [_noop_transition]

    def test_decorated_function_is_invoked_with_the_outgoing_and_incoming_resource(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")
        seen: list[tuple[BaseResource, BaseResource]] = []

        @ResourceManager.register_transition(old_cls, new_cls)
        def transition(old: BaseResource, new: BaseResource) -> None:
            seen.append((old, new))

        old = old_cls()
        new = new_cls()
        ResourceManager.run_resource_transitions(old, new)

        assert seen == [(old, new)]


@pytest.mark.usefixtures("_clean_transitions")
class TestResourceManagerRunResourceTransitions:
    """ResourceManager.run_resource_transitions class method."""

    def test_no_registration_is_a_noop(self):
        ResourceManager.run_resource_transitions(
            _make_resource_class("OldRes")(), _make_resource_class("NewRes")()
        )

    def test_runs_every_transition_in_registration_order(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")
        calls: list[str] = []

        def first(old: BaseResource, new: BaseResource) -> None:
            calls.append("first")

        def second(old: BaseResource, new: BaseResource) -> None:
            calls.append("second")

        ResourceManager.register_transition(old_cls, new_cls, first)
        ResourceManager.register_transition(old_cls, new_cls, second)

        ResourceManager.run_resource_transitions(old_cls(), new_cls())

        assert calls == ["first", "second"]

    def test_only_the_matching_class_pair_fires(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")
        other_cls = _make_resource_class("OtherRes")
        calls: list[str] = []

        def transition(old: BaseResource, new: BaseResource) -> None:
            calls.append("fired")

        ResourceManager.register_transition(old_cls, new_cls, transition)

        ResourceManager.run_resource_transitions(old_cls(), other_cls())
        ResourceManager.run_resource_transitions(other_cls(), new_cls())

        assert calls == []

    def test_exact_type_match_ignores_subclasses(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")
        sub_old_cls = type("SubOldRes", (old_cls,), {})
        calls: list[str] = []

        def transition(old: BaseResource, new: BaseResource) -> None:
            calls.append("fired")

        ResourceManager.register_transition(old_cls, new_cls, transition)

        ResourceManager.run_resource_transitions(sub_old_cls(), new_cls())

        assert calls == []

    def test_raising_transition_propagates(self):
        old_cls = _make_resource_class("OldRes")
        new_cls = _make_resource_class("NewRes")

        def transition(old: BaseResource, new: BaseResource) -> None:
            raise RuntimeError("boom")

        ResourceManager.register_transition(old_cls, new_cls, transition)

        with pytest.raises(RuntimeError, match="boom"):
            ResourceManager.run_resource_transitions(old_cls(), new_cls())


class TestResourceManagerEvaluateTarget:
    """ResourceManager.evaluate_target() — resolving targets to per-display resources."""

    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_resource_creates_per_display_instance(self, mock_cs, tmp_path):
        displays = [
            _make_display_info(r"\\?\DISPLAY#A#{path-a}", x=0),
            _make_display_info(r"\\?\DISPLAY#B#{path-b}", model="Monitor B", x=1920),
        ]
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": str(tmp_path / "test.png"), "style": "fill"},
            )
        }

        result = ResourceManager.evaluate_target("wp1", displays)

        assert len(result) == 2
        assert all(isinstance(v, DisplayScene) for v in result)
        assert all(isinstance(v.resource, BaseResource) for v in result)
        assert {v.display_id for v in result} == {d.display_id for d in displays}

    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_unknown_target_raises(self, mock_cs):
        mock_cs.instance.resource = {}
        mock_cs.instance.scene = {}

        with pytest.raises(ValueError, match="target unknown not found"):
            ResourceManager.evaluate_target("unknown", [])

    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_scene_resolves_per_display(self, mock_cs, tmp_path):
        display = _make_display_info(r"\\?\DISPLAY#A#{path-a}", model="Dell U27")
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": str(tmp_path / "test.png"), "style": "fill"},
            )
        }
        mock_cs.instance.scene = {
            "office": SceneConfig(
                bindings=[SceneBinding(match_display_model="Dell.*", resource="wp1")]
            )
        }

        result = ResourceManager.evaluate_target("office", [display])

        assert any(v.display_id == display.display_id for v in result)

    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_no_displays_returns_empty(self, mock_cs):
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": "/fake/path.png", "style": "fill"},
            )
        }

        result = ResourceManager.evaluate_target("wp1", [])

        assert result == []


class TestResourceManagerEvaluateScene:
    """ResourceManager.evaluate_scene() — mapping displays to resources by model."""

    def test_matches_by_model_pattern(self):
        binding = SceneBinding(match_display_model="Dell.*", resource="dell_wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model="Dell U2719D")

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {display.display_id: binding}

    def test_no_match_returns_empty(self):
        binding = SceneBinding(display_model="NonExistent", resource="wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model="Dell U2719D")

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {}

    def test_none_model_is_skipped(self):
        binding = SceneBinding(match_display_model=".*", resource="wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model=None)  # type: ignore[arg-type]

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {}

    def test_multiple_displays_first_match_wins(self):
        binding = SceneBinding(match_display_model="Monitor", resource="wp")
        displays = [
            _make_display_info(r"\\?\DISPLAY#A#{path-a}", model="Monitor A"),
            _make_display_info(r"\\?\DISPLAY#B#{path-b}", model="Monitor B"),
        ]

        result = ResourceManager.evaluate_scene([binding], displays)

        assert result == {displays[0].display_id: binding}
