import atexit
import getpass
import json
import logging
import os
import re
import subprocess
import time
import winreg
from pathlib import Path
from typing import ClassVar

import win32event

from ..util.process_util import (
    close_and_wait_for_exit,
    get_executable_path,
    get_running_processes,
    post_close_message,
)
from ..util.singleton_meta import SingletonMeta
from .base_resource import BaseResource
from .resource_cycle import ResourceCycle
from .static_wallpaper import StaticWallpaper

logger = logging.getLogger(__name__)


_WE_EXE_NAMES = ("wallpaper32.exe", "wallpaper64.exe")
_WE_CLI_TIMEOUT_SECONDS = 10.0
_WE_HOST_RACE_TIMEOUT_SECONDS = 3.0
_WE_HOST_SETTLE_SECONDS = 0.25


def get_steam_libraries() -> list[Path]:
    """Find all Steam library directories on the system.

    Searches the Windows registry, common default install locations across
    drives, and ``libraryfolders.vdf``.

    Returns:
        Every discovered Steam library root (a directory containing
        ``steamapps/``), deduplicated. Empty when no Steam install is found.
    """
    steam_paths: set[Path] = set()

    # 1. get Steam installation path from the Windows Registry
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
    ]
    for hkey, subkey in reg_paths:
        try:
            with winreg.OpenKey(hkey, subkey) as key:
                install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                if install_path:
                    path_obj = Path(install_path)
                    if path_obj.exists():
                        steam_paths.add(path_obj.resolve())
        except OSError:
            continue

    # 2. check common default installation directories on every drive
    for root in os.listdrives():
        for p in [
            Path(root) / "Program Files (x86)" / "Steam",
            Path(root) / "Steam",
            Path(root) / "SteamLibrary",
        ]:
            if p.exists():
                steam_paths.add(p.resolve())

    # 3. parse libraryfolders.vdf to discover additional multi-drive libraries
    all_libraries: set[Path] = set(steam_paths)
    for steam_path in steam_paths:
        vdf_paths = [
            steam_path / "steamapps" / "libraryfolders.vdf",
            steam_path / "config" / "libraryfolders.vdf",
        ]
        for vdf_path in vdf_paths:
            if vdf_path.exists():
                try:
                    content = vdf_path.read_text(encoding="utf-8", errors="ignore")
                    matches = re.findall(r'"path"\s+"([^"]+)"', content, re.IGNORECASE)
                    for lib in matches:
                        clean_path = lib.replace(r"\\\\", "\\")
                        lib_path = Path(clean_path)
                        if lib_path.exists():
                            all_libraries.add(lib_path.resolve())
                except Exception:
                    pass

    return list(all_libraries)


def find_wallpaper_engine_executables_in_library(lib_path: Path) -> list[Path]:
    """Search a Steam library and return every Wallpaper Engine exe found.

    Args:
        lib_path: Root of a Steam library (directory containing ``steamapps/``).

    Returns:
        Paths to ``wallpaper32.exe`` / ``wallpaper64.exe`` that exist on disk.
        Empty when no install is present in this library.
    """
    common_dir = lib_path / "steamapps" / "common"
    found: set[Path] = set()

    def _check_folder(folder: Path) -> None:
        for exe_name in _WE_EXE_NAMES:
            candidate = folder / exe_name
            if candidate.exists() and candidate not in found:
                found.add(candidate)

    _check_folder(common_dir / "wallpaper_engine")

    # check via AppID manifest (431960) to handle custom folder names
    acf_path = lib_path / "steamapps" / "appmanifest_431960.acf"
    if acf_path.exists():
        try:
            acf_content = acf_path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r'"installdir"\s+"([^"]+)"', acf_content, re.IGNORECASE)
            if match:
                _check_folder(common_dir / match.group(1))
        except Exception:
            pass

    return list(found)


def resolve_wallpaper_engine_executables() -> list[Path]:
    """Find every Wallpaper Engine exe across all discovered Steam libraries.

    Returns:
        Every ``wallpaper32.exe`` / ``wallpaper64.exe`` path found. Empty when
        no install is present.
    """
    libraries = get_steam_libraries()
    all_exes: list[Path] = []
    for library in libraries:
        all_exes.extend(find_wallpaper_engine_executables_in_library(library))
    return all_exes


def _find_running_wallpaper_engines() -> list[tuple[int, Path]]:
    """Return ``(pid, exe_path)`` for every running Wallpaper Engine instance.

    Only executables that live in a discovered Steam library are tracked, so a
    process that merely shares the executable name is not mistaken for one.

    Returns:
        One pair per matching process, in snapshot order. Empty when none is
        running.

    Raises:
        RuntimeError: The process snapshot could not be taken.
    """
    engine_executables = resolve_wallpaper_engine_executables()
    processes = get_running_processes()
    matches: list[tuple[int, Path]] = []
    for pid, name in processes:
        if name.lower() not in _WE_EXE_NAMES:
            continue
        path = get_executable_path(pid)
        if path is None:
            continue
        if path not in engine_executables:
            continue
        matches.append((pid, path))
    return matches


class WallpaperEngineApp(metaclass=SingletonMeta):
    def __init__(self, exe_path: Path | str | None = None) -> None:
        self.exe_path: Path | None = Path(exe_path) if exe_path is not None else None
        self._original_monitor_detection: str | None = None
        self._popen: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Resolve the exe path, enforce monitor detection, then restart Wallpaper
        Engine and hold the new instance.

        Raises:
            FileNotFoundError: No Wallpaper Engine exe was found and none was
                supplied, or the resolved exe's directory has no ``config.json``.
            RuntimeError: The process snapshot could not be taken, or no host
                could be started (see :meth:`_start_host`).
        """
        exe_path = self.exe_path
        running_instance = _find_running_wallpaper_engines()
        if exe_path is None:
            if running_instance:
                exe_path = running_instance[0][1]
            else:
                exes = resolve_wallpaper_engine_executables()
                if not exes:
                    raise FileNotFoundError(
                        "wallpaper engine executable (wallpaper32.exe / wallpaper64.exe) not found"
                    )
                exe_path = exes[0]
        self.exe_path = exe_path
        logger.debug(f"wallpaper engine executable resolved to {exe_path}")

        config_path = exe_path.parent / "config.json"
        if not config_path.exists():
            raise FileNotFoundError("wallpaper engine config file (config.json) not found")
        self.config_path: Path = config_path

        self.enforce_monitor_detection_devicepath()

        close_and_wait_for_exit([pid for pid, _ in running_instance])
        self._popen = self._start_host()
        logger.debug(f"wallpaper engine instance start (pid: {self._popen.pid})")

    def _start_host(self) -> subprocess.Popen[bytes]:
        """Return the process hosting Wallpaper Engine.

        Every running instance must be closed before calling this: a spawn that
        races an existing one only forwards to it and exits, so the race cannot
        tell a host from a forwarder.

        Returns:
            The candidate observed to have taken the single-instance slot.

        Raises:
            RuntimeError: The race left no host — neither candidate exited within
                the timeout, or the survivor exited as well, meaning an instance
                this call did not start already owns the desktop.
        """
        assert self.exe_path is not None

        candidates = [
            subprocess.Popen(
                [str(self.exe_path), "-silent"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS,
            )
            for _ in range(2)
        ]

        handles = [p._handle for p in candidates]  # type: ignore[attr-defined]

        try:
            # 1. Wait for one of the two candidates to exit, leaving a single host
            result = win32event.WaitForMultipleObjects(
                handles,
                False,
                int(_WE_HOST_RACE_TIMEOUT_SECONDS * 1000),
            )

            # If the wait timed out, neither candidate exited in time, which is unexpected.
            if result == win32event.WAIT_TIMEOUT:
                raise RuntimeError(
                    "wallpaper engine host race timeout: neither process exited "
                    f"within {_WE_HOST_RACE_TIMEOUT_SECONDS}s"
                )

            # 2. Determine which candidate exited and which survived
            exited = result - win32event.WAIT_OBJECT_0
            survivor_index = next(i for i in range(len(candidates)) if i != exited)

            # 3. Wait for the survivor to settle (i.e., ensure it's not a forwarder
            # that will exit soon)
            settle_result = win32event.WaitForMultipleObjects(
                [handles[survivor_index]],
                False,
                int(_WE_HOST_SETTLE_SECONDS * 1000),
            )

            # If the survivor exited during the settle wait, it means it was a
            # forwarder and not a host, which is unexpected.
            if settle_result != win32event.WAIT_TIMEOUT:
                raise RuntimeError(
                    "wallpaper engine host failed to settle: survivor exited unexpectedly"
                )

            survivor = candidates[survivor_index]
            logger.info(f"wallpaper engine holding host process {survivor.pid}")
            return survivor

        except Exception as e:
            for candidate in candidates:
                if candidate.poll() is None:  # process is still running
                    logger.warning(
                        f"wallpaper engine cleaning up process {candidate.pid} due to error"
                    )
                    post_close_message(candidate.pid)
            raise e

    def stop(self) -> None:
        """Close the instance this app started, then restore ``monitordetection``."""
        popen, self._popen = self._popen, None
        if popen is not None:
            close_and_wait_for_exit([popen.pid])
        self.restore_monitor_detection()

    def apply_cli(self, cmd: list[str]) -> None:
        """Run a Wallpaper Engine CLI command, waiting on it only up to a timeout.

        Args:
            cmd: Full argv list, starting with the Wallpaper Engine executable.

        Raises:
            subprocess.TimeoutExpired: The client did not return within
                ``_WE_CLI_TIMEOUT_SECONDS``, so no host answered it.
        """
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS,
        )
        process.wait(timeout=_WE_CLI_TIMEOUT_SECONDS)

    def open_wallpaper(
        self,
        file: Path | str,
        location: str | None = None,
        monitor: str | int | None = None,
        play_in_window: str | None = None,
        width: int | None = None,
        height: int | None = None,
        x: int | None = None,
        y: int | None = None,
        preset: str | None = None,
    ) -> None:
        """Open a wallpaper on the monitor chosen by ``location`` or ``monitor``.

        When neither is given, Wallpaper Engine loads it on the first monitor.

        Args:
            file: Path to the project.json of the wallpaper to open, or a direct
                path to a wallpaper file (e.g. .mp4).
            location: Internal identifier of the monitor from Wallpaper Engine's
                config.json.
            monitor: Index of the monitor to load the wallpaper on, beginning at 0.
            play_in_window: Window name when opening the wallpaper in a pop-out
                window. The Wallpaper Engine default is "Wallpaper Pop-out".
            width: Width of the pop-out window.
            height: Height of the pop-out window.
            x: Horizontal position of the pop-out window.
            y: Vertical position of the pop-out window.
            preset: Name of a local preset (must have been saved previously).
        """
        cmd: list[str] = [
            str(self.exe_path),
            "-control",
            "openWallpaper",
            "-file",
            str(file),
        ]
        for flag, value in (
            ("-location", location),
            ("-monitor", f'"{monitor}"' if isinstance(monitor, str) else monitor),
            ("-playInWindow", play_in_window),
            ("-width", width),
            ("-height", height),
            ("-x", x),
            ("-y", y),
            ("-preset", preset),
        ):
            if value is not None:
                cmd.extend([flag, str(value)])
        self.apply_cli(cmd)

    def play_wallpaper(self) -> None:
        """Resume all wallpapers from pause or stop."""
        cmd = [str(self.exe_path), "-control", "play"]
        self.apply_cli(cmd)

    def stop_wallpaper(self) -> None:
        """Stop all wallpapers, leaving the Wallpaper Engine process running."""
        cmd = [str(self.exe_path), "-control", "stop"]
        self.apply_cli(cmd)

    def close_wallpaper(
        self,
        location: str | None = None,
        monitor: str | int | None = None,
    ) -> None:
        """Remove the wallpaper from a monitor or window.

        When neither ``location`` nor ``monitor`` is given, Wallpaper Engine
        removes all wallpapers (its default behaviour).

        Args:
            location: Internal identifier of the monitor or window name from
                Wallpaper Engine's config.json.
            monitor: Index of the monitor to close the wallpaper from,
                beginning at 0.
        """
        cmd: list[str] = [str(self.exe_path), "-control", "closeWallpaper"]
        for flag, value in (
            ("-location", location),
            ("-monitor", f'"{monitor}"' if isinstance(monitor, str) else monitor),
        ):
            if value is not None:
                cmd.extend([flag, str(value)])
        self.apply_cli(cmd)

    def enforce_monitor_detection_devicepath(self) -> bool:
        """Ensure ``monitordetection`` in Wallpaper Engine's config.json is "devicepath".

        When the value is already "devicepath" the file is left untouched.
        Otherwise the original is saved on ``self._original_monitor_detection``
        (for :meth:`restore_monitor_detection`) and overwritten.

        Returns:
            True when the config file was changed, False when monitordetection
            was already "devicepath".

        Raises:
            KeyError: The config has no ``general.user`` section for the current
                OS user, or that section has no ``monitordetection`` key.
            json.JSONDecodeError: The config.json is not valid JSON.
        """
        config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        user_section = config_data[getpass.getuser()]["general"]["user"]
        if "monitordetection" not in user_section:
            raise KeyError("wallpaper engine config: no 'monitordetection' in the user section")
        original = user_section["monitordetection"]
        if original == "devicepath":
            logger.debug("wallpaper engine monitordetection config is already 'devicepath'")
            return False
        logger.debug(f"wallpaper engine monitordetection config {original!r} -> 'devicepath'")
        user_section["monitordetection"] = "devicepath"
        self.config_path.write_text(
            json.dumps(config_data, indent="\t"),
            encoding="utf-8",
        )
        self._original_monitor_detection = original
        return True

    def restore_monitor_detection(self) -> None:
        """Restore the ``monitordetection`` captured by
        :meth:`enforce_monitor_detection_devicepath`.

        No-op if nothing was captured (``self._original_monitor_detection`` is
        None).

        Raises:
            KeyError: The config has no ``general.user`` section for the current
                OS user.
            json.JSONDecodeError: The config.json is not valid JSON.
        """
        if self._original_monitor_detection is None:
            return
        config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        user_section = config_data[getpass.getuser()]["general"]["user"]
        user_section["monitordetection"] = self._original_monitor_detection
        logger.info(
            f"wallpaper engine monitordetection restored to {self._original_monitor_detection!r}"
        )
        self.config_path.write_text(
            json.dumps(config_data, indent="\t"),
            encoding="utf-8",
        )
        self._original_monitor_detection = None


class WallpaperEngineResource(BaseResource):
    app: ClassVar[WallpaperEngineApp | None] = None

    def __init__(self, path: Path | str) -> None:
        super().__init__()
        self.image_path = Path(path)
        self._demounted: bool = False

    @classmethod
    def _ensure_app(cls) -> WallpaperEngineApp:
        """Return the shared :class:`WallpaperEngineApp`, starting it on first use.

        :meth:`WallpaperEngineApp.stop` is registered with :mod:`atexit` here, as
        the owner of the shared instance, so the Wallpaper Engine process this
        resource caused to start is closed and ``monitordetection`` restored when
        the process exits. One callback does both, in order, so nothing depends
        on ``atexit`` registration order.

        Registered *before* :meth:`WallpaperEngineApp.start`, because ``start`` is
        what may rewrite ``config.json``: the teardown must be armed before
        anything can change, so a ``start`` that raises part-way still gets its
        change reverted. Registration happens once, with the instance.

        Returns:
            The shared :class:`WallpaperEngineApp` instance stored on
            :attr:`app`.
        """
        if cls.app is None:
            cls.app = WallpaperEngineApp()
            atexit.register(cls.app.stop)
            cls.app.start()
        return cls.app

    def mount(self) -> None:
        """Open the wallpaper on the bound display and resume playback."""
        app = WallpaperEngineResource._ensure_app()
        assert self.display is not None
        app.open_wallpaper(
            file=self.image_path,
            monitor=self.display.monitor_device_path,
        )
        app.play_wallpaper()

    def demount(self) -> None:
        """Close the wallpaper on the bound display, leaving other monitors alone.

        Idempotent: the built-in engine-to-static transition demounts the engine
        itself and the framework's lifecycle demounts it again right after.  A
        second ``closeWallpaper`` would cost a CLI round trip and could undo the
        composite the transition applied in between, so a repeat call returns
        without dispatching anything.

        Raises:
            RuntimeError: The shared app was never initialized, or no display is
                bound. Both are unreachable through the lifecycle
                :class:`BaseResource` guarantees — ``mount()`` always runs first,
                and it initializes the app and binds the display — so either one
                means an internal error rather than "nothing to close".
        """
        if self._demounted:
            return
        if WallpaperEngineResource.app is None:
            raise RuntimeError("wallpaper engine app not initialized")
        if self.display is None:
            raise RuntimeError("display not bound")
        WallpaperEngineResource.app.close_wallpaper(
            monitor=self.display.monitor_device_path,
        )
        self._demounted = True


def hand_over_to_static(old: BaseResource, new: BaseResource) -> None:
    """Put the incoming static wallpaper on screen before the engine releases the desktop.

    Registered by :mod:`wallpaper_auto.resource_manager` for the exact pairs
    ``(WallpaperEngineResource, StaticWallpaper)`` and
    ``(ResourceCycle, StaticWallpaper)`` — it lives here, next to the engine
    lifecycle it drives.  This module does not import ``resource_manager``: the
    resource modules are imported *by* it, so importing it back at the top level
    would be a cycle (the same reason ``resource_cycle`` defers its import).

    The engine paints over the desktop, so the framework's own order — demount
    the engine, then mount the incoming resource, whose image stays buffered
    until the worker loop composites it — leaves the previous wallpaper visible
    for the whole (slow) engine close.  Mounting the incoming resource here
    buffers and composites its image first, waits for the engine CLI to take
    effect, and only then demounts the engine, so the engine releases the
    desktop straight onto the new image.

    Args:
        old: The outgoing engine resource, demounted here.
        new: The incoming static resource, mounted and composited here.
    """
    new.mount()
    new.immediate_plot_canvas()
    time.sleep(2)  # wait wallpaper apply to task effect
    old.demount()


def register_builtin_transitions() -> None:
    """Register the built-in hand-over transitions.

    Called by :mod:`wallpaper_auto.resource_manager` at import time, so the
    registration lives with the resource it belongs to.  The import is deferred
    because this module is imported *by* ``resource_manager``; referring to it at
    module level would be a cycle, the same reason ``resource_cycle`` defers its
    import.

    Both outgoing resources are demounted by ``hand_over_to_static`` and then
    demounted again by the framework, so their ``demount`` must be idempotent:
    ``WallpaperEngineResource`` flags it explicitly, and ``ResourceCycle`` is
    already a no-op once its thread has been joined.
    """
    from ..resource_manager import ResourceManager  # noqa: PLC0415

    ResourceManager.register_transition(
        WallpaperEngineResource, StaticWallpaper, hand_over_to_static
    )
    ResourceManager.register_transition(ResourceCycle, StaticWallpaper, hand_over_to_static)
