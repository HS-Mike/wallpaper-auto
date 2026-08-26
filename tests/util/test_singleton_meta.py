"""Tests for singleton_meta.py — covers SingletonMeta metaclass."""

import threading
import time

import pytest

from wallpaper_auto.util.singleton_meta import SingletonMeta

# ── helpers ──────────────────────────────────────────────────────────────


class _ResetMeta(type):
    """Metaclass that auto-clears singleton state in each test run.

    Because ``SingletonMeta`` classes hold module-level state, we define
    test-only classes inside each test method so every run starts fresh.
    """


# ── basic singleton behavior ────────────────────────────────────────────


class TestSingletonCreation:
    """Tests for single-instance enforcement."""

    def test_first_instantiation_succeeds(self):
        class TestClass(metaclass=SingletonMeta):
            def __init__(self, value=0):
                self.value = value

        inst = TestClass(value=42)
        assert inst.value == 42

    def test_second_instantiation_raises(self):
        class TestClass(metaclass=SingletonMeta):
            def __init__(self, value=0):
                self.value = value

        TestClass(value=42)
        with pytest.raises(RuntimeError) as exc:
            TestClass(value=100)
        assert "cannot be instantiated multiple times" in str(exc.value)

    def test_instance_property_returns_same_object(self):
        class TestClass(metaclass=SingletonMeta):
            def __init__(self):
                self.data = "hello"

        inst1 = TestClass()
        inst2 = TestClass.instance
        assert inst2 is inst1
        assert inst2.data == "hello"


class TestHasInstance:
    """Tests for ``has_instance()`` classmethod."""

    def test_returns_false_before_creation(self):
        class TestClass(metaclass=SingletonMeta):
            pass

        assert TestClass.has_instance() is False

    def test_returns_true_after_creation(self):
        class TestClass(metaclass=SingletonMeta):
            pass

        TestClass()
        assert TestClass.has_instance() is True

    def test_returns_false_after_clear(self):
        class TestClass(metaclass=SingletonMeta):
            pass

        TestClass()
        TestClass.clear_instance()
        assert TestClass.has_instance() is False


class TestClearInstance:
    """Tests for ``clear_instance()`` classmethod."""

    def test_clear_before_creation_returns_false(self):
        class TestClass(metaclass=SingletonMeta):
            pass

        assert TestClass.clear_instance() is False

    def test_clear_after_creation_returns_true(self):
        class TestClass(metaclass=SingletonMeta):
            def __init__(self):
                self.name = "test"

        TestClass()
        assert TestClass.clear_instance() is True

    def test_clear_then_recreate(self):
        class TestClass(metaclass=SingletonMeta):
            def __init__(self, version=1):
                self.version = version

        inst1 = TestClass(version=1)
        TestClass.clear_instance()
        inst2 = TestClass(version=2)

        assert inst2 is not inst1
        assert inst2.version == 2
        assert TestClass.instance is inst2

    def test_double_clear_returns_false(self):
        class TestClass(metaclass=SingletonMeta):
            pass

        TestClass()
        assert TestClass.clear_instance() is True
        assert TestClass.clear_instance() is False


# ── instance property edge cases ────────────────────────────────────────


class TestInstanceProperty:
    """Tests for the ``instance`` property accessor."""

    def test_access_before_creation_raises(self):
        class TestClass(metaclass=SingletonMeta):
            pass

        with pytest.raises(RuntimeError) as exc:
            _ = TestClass.instance
        assert "has not been instantiated yet" in str(exc.value)
        assert "TestClass" in str(exc.value)

    def test_error_message_contains_class_name(self):
        class MySpecialSingleton(metaclass=SingletonMeta):
            pass

        with pytest.raises(RuntimeError) as exc:
            _ = MySpecialSingleton.instance
        assert "MySpecialSingleton" in str(exc.value)


# ── thread safety ───────────────────────────────────────────────────────


class TestThreadSafety:
    """Tests that only one instance is created under concurrent access."""

    def test_concurrent_instantiation(self):
        class TestClass(metaclass=SingletonMeta):
            def __init__(self):
                time.sleep(0.02)
                self.counter = 0

        instances: list = []
        errors: list = []

        def create() -> None:
            try:
                instances.append(TestClass())
            except RuntimeError as e:
                errors.append(e)

        threads = [threading.Thread(target=create) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(instances) == 1
        assert len(errors) == 4
        for err in errors:
            assert "cannot be instantiated multiple times" in str(err)


# ── independent singleton classes ───────────────────────────────────────


class TestMultipleSingletons:
    """Tests that separate classes each have their own singleton state."""

    def test_two_classes_are_independent(self):
        class ClassA(metaclass=SingletonMeta):
            def __init__(self):
                self.tag = "A"

        class ClassB(metaclass=SingletonMeta):
            def __init__(self):
                self.tag = "B"

        a1 = ClassA()
        b1 = ClassB()

        assert a1.tag == "A"
        assert b1.tag == "B"
        assert ClassA.instance is a1
        assert ClassB.instance is b1
        assert ClassA.instance is not ClassB.instance


class TestInheritance:
    """Tests that base and derived classes maintain separate singletons."""

    def test_base_and_derived_are_independent(self):
        class Base(metaclass=SingletonMeta):
            def __init__(self):
                self.base_val = "base"

        class Derived(Base):
            def __init__(self):
                super().__init__()
                self.derived_val = "derived"

        base_inst = Base()
        derived_inst = Derived()

        assert base_inst.base_val == "base"
        assert derived_inst.base_val == "base"
        assert derived_inst.derived_val == "derived"

        assert Base.instance is base_inst
        assert Derived.instance is derived_inst
        assert base_inst is not derived_inst


# ── initialization edge cases ───────────────────────────────────────────


class TestInitializationEdgeCases:
    """Tests for various constructor signatures and patterns."""

    def test_no_init(self):
        class SimpleClass(metaclass=SingletonMeta):
            pass

        inst1 = SimpleClass()
        inst2 = SimpleClass.instance
        assert inst2 is inst1

    def test_with_arguments(self):
        class Config(metaclass=SingletonMeta):
            def __init__(self, host="localhost", port=8080):
                self.host = host
                self.port = port

        cfg = Config(host="example.com", port=9000)
        assert cfg.host == "example.com"
        assert cfg.port == 9000

        with pytest.raises(RuntimeError):
            Config(host="other.com", port=8000)

        assert Config.instance.host == "example.com"

    def test_complex_init(self):
        class Complex(metaclass=SingletonMeta):
            def __init__(self):
                self.data = {}
                self._setup()

            def _setup(self) -> None:
                self.data["key"] = "value"

        inst = Complex()
        assert inst.data["key"] == "value"
        assert Complex.instance.data["key"] == "value"
