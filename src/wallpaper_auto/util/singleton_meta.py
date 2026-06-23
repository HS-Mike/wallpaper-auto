import threading
from typing import Dict, Type, Any, TypeVar, Optional, cast, NoReturn

T = TypeVar('T')


class SingletonMeta(type):
    """
    A thread-safe Singleton metaclass that enforces explicit instantiation.
    """
    _meta_lock: threading.Lock = threading.Lock()

    def __init__(cls, name: str, bases: tuple[type, ...], dct: dict[str, Any]) -> None:
        super().__init__(name, bases, dct)
        cls._instance = None
        cls._instance_lock = threading.Lock()

    def __call__(cls: Type[T], *args: Any, **kwargs: Any) -> T:
        meta_cls = cast(SingletonMeta, cls)
        
        if meta_cls._instance is not None:
            meta_cls._raise_duplicate_error()

        with meta_cls._instance_lock:
            # Re-check inside the lock (Double-checked locking)
            if meta_cls._instance is None:
                ins = super().__call__(*args, **kwargs)
                meta_cls._instance = ins
                return cast(T, ins)
            
            # If it's not None inside the lock, it means another thread 
            # initialized it right before we acquired the lock.
            meta_cls._raise_duplicate_error()

    @property
    def instance(cls: Type[T]) -> T:
        """
        Global access point. Returns the exact subclass type T.
        """
        meta_cls = cast(SingletonMeta, cls)
        if meta_cls._instance is not None:
            return cast(T, meta_cls._instance)
        
        raise RuntimeError(
            f"Class '{cls.__name__}' has not been instantiated yet. "
            f"Please create the instance first using `{cls.__name__}(...)`."
        )

    def has_instance(cls: Type[Any]) -> bool:
        return cast(SingletonMeta, cls)._instance is not None

    def clear_instance(cls: Type[Any]) -> bool:
        meta_cls = cast(SingletonMeta, cls)
        with meta_cls._instance_lock:
            if meta_cls._instance is not None:
                meta_cls._instance = None
                return True
            return False

    def _raise_duplicate_error(cls) -> NoReturn:
        """
        By declaring NoReturn, the type checker understands that 
        this function never returns normally, satisfying all code path requirements.
        """
        raise RuntimeError(
            f"Class '{cls.__name__}' is a singleton and cannot be instantiated multiple times. "
            f"Use `{cls.__name__}.instance` to access the existing instance."
        )
