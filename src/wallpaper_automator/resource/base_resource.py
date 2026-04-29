"""
Base resource abstract class.

This module defines the abstract base class for all wallpaper resource types.

All resource class must inherit from BaseResource and implement mount/demount interface
"""

import atexit
import os
import shutil
import tempfile
import uuid
from abc import ABC, ABCMeta, abstractmethod


class _BaseResourceMeta(ABCMeta):
    """
    Metaclass for BaseResource that manages a shared process-level cache directory.

    On the first BaseResource subclass to be instantiated, this metaclass:
    1. Creates a process-unique temporary cache directory at
       ``/tmp/wallpaper_automator_cache_<pid>/``
    2. Registers an ``atexit`` hook to delete the entire cache tree on
       process termination.

    This ensures that all resource instances share a common cache root and
    that no temporary files are left behind when the program exits.

    """

    _base_cache_dir = os.path.join(tempfile.gettempdir(), f"wallpaper_automator_cache_{os.getpid()}")
    _base_cache_initialized = False

    def __new__(mcs, name, bases, namespace):
        """Create a new BaseResource subclass and initialize the cache once."""
        cls = super().__new__(mcs, name, bases, namespace)
        if not mcs._base_cache_initialized:
            os.makedirs(cls._base_cache_dir, exist_ok=True)
            atexit.register(cls._cleanup_base_cache)
            mcs._base_cache_initialized = True
        return cls

    @classmethod
    def _cleanup_base_cache(cls) -> None:
        """Remove the entire base cache directory on process exit."""
        if os.path.exists(cls._base_cache_dir):
            shutil.rmtree(cls._base_cache_dir)


class BaseResource(ABC, metaclass=_BaseResourceMeta):
    """
    Abstract base class for wallpaper resources.

    All resource types must inherit from this class and implement the
    mount/demount lifecycle methods.

    Each instance can optionally be allocated a dedicated subdirectory
    under the shared process cache (enabled by default). This cache
    directory can be used to store temporary files such as downloaded
    images, extracted archives, etc.
    """

    def __init__(self, temp_dir: bool = True):
        """Initialize the resource with an optional instance cache directory."""
        self.temp_file = temp_dir
        self._cache_dir = None
        if temp_dir:
            self._cache_dir = os.path.join(self.__class__._base_cache_dir, str(uuid.uuid4()))
            os.makedirs(self._cache_dir, exist_ok=True)

    @property
    def cache_dir(self) -> str:
        """Path to this instance's dedicated cache directory.

        Use this property to locate the instance-specific temporary directory
        created during __init__. Files written here are cleaned up
        automatically when the process exits.

        Returns:
            The absolute path to the cache directory.

        Raises:
            ValueError: If temp_dir was set to False during initialization,
                        meaning no cache directory was allocated.
        """
        if self._cache_dir is None:
            raise ValueError("cache dir is unavailable")
        return self._cache_dir

    @abstractmethod
    def mount(self) -> None:
        """
        Prepare and make the wallpaper resource available.

        The wallpaper system calls mount() before applying a wallpaper
        and demount() after the wallpaper has been applied or when
        switching to a different wallpaper.
        """
        ...

    @abstractmethod
    def demount(self) -> None:
        """
        Release and clean up the wallpaper resource.

        Subclasses implement this to release any resources held during
        the mount phase.

        The wallpaper system guarantees that demount() is always called
        after mount(), even if an error occurs during wallpaper application.
        """
        ...
