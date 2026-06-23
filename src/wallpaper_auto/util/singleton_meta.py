import threading
from typing import Any, TypeVar, Optional

T = TypeVar('T')

class SingletonMeta(type):
    """
    A thread-safe Singleton metaclass that enforces explicit instantiation.
    
    - First instantiation: `obj = MyClass(*args, **kwargs)`
    - Subsequent access: `obj = MyClass.instance`
    - Triggering `MyClass()` again will raise a `RuntimeError`.
    
    Each subclass maintains its own isolated singleton instance.
    """
    _meta_lock: threading.Lock = threading.Lock()

    def __init__(cls, name: str, bases: tuple[type, ...], dct: dict[str, Any]) -> None:
        super().__init__(name, bases, dct)
        # Allocate isolated instance slot and lock for each unique class
        cls._instance: Optional[Any] = None
        cls._instance_lock: threading.Lock = threading.Lock()

    def __call__(cls: "SingletonMeta", *args: Any, **kwargs: Any) -> Any:
        # Double-checked locking for thread safety
        if cls._instance is not None:
            cls._raise_duplicate_error()

        with cls._instance_lock:
            if cls._instance is None:
                ins = super().__call__(*args, **kwargs)
                cls._instance = ins
                return ins
            else:
                cls._raise_duplicate_error()

    @property
    def instance(cls: "SingletonMeta") -> Any:
        """
        Global access point to get the existing singleton instance.
        Raises RuntimeError if the class has not been instantiated yet.
        """
        if cls._instance is not None:
            return cls._instance
        raise RuntimeError(
            f"Class '{cls.__name__}' has not been instantiated yet. "
            f"Please create the instance first using `{cls.__name__}(...)`."
        )

    def has_instance(cls: "SingletonMeta") -> bool:
        """Check whether the singleton instance has been initialized."""
        return cls._instance is not None

    def clear_instance(cls: "SingletonMeta") -> bool:
        """
        Clear the current singleton instance. 
        Mainly used to reset state between unit tests.
        Returns True if an instance was cleared, False otherwise.
        """
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance = None
                return True
            return False

    def _raise_duplicate_error(cls) -> None:
        raise RuntimeError(
            f"Class '{cls.__name__}' is a singleton and cannot be instantiated multiple times. "
            f"Use `{cls.__name__}.instance` to access the existing instance."
        )
    