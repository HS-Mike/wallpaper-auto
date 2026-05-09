"""
Base resource abstract class.

This module defines the abstract base class for all wallpaper resource types.

All resource class must inherit from BaseResource and implement mount/demount interface
"""

from abc import ABC, abstractmethod


class BaseResource(ABC):
    """
    Abstract base class for wallpaper resources.

    All resource types must inherit from this class and implement the
    mount/demount lifecycle methods.
    """

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
