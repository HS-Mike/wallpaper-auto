import atexit
import getpass
import json
import logging
import os
import re
import subprocess
import winreg
from pathlib import Path
from typing import ClassVar

import win32con
import win32gui
import win32process

from ..util.process_util import get_executable_path, get_running_processes
from ..util.singleton_meta import SingletonMeta
from .base_resource import BaseResource

logger = logging.getLogger(__name__)

#: Executable basenames Wallpaper Engine ships; checked in this order.
_WE_EXE_NAMES = ("wallpaper32.exe", "wallpaper64.exe")


def get_steam_libraries() -> list[Path]:
    """Find all Steam library directories on the system.

    Searches the Windows registry, common default install locations across
    drives, and ``libraryfolders.vdf``.

    Returns:
        Every discovered Steam library root (a directory containing
        ``steamapps/``), deduplicated. Empty when no Steam install is found.
    """
    steam_paths: set[Path] = set()

    # 1. Try to get Steam installation path from the Windows Registry
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
        except WindowsError:
            continue

    # 2. Fallback: check common default installation directories across drives
    for drive in ["C", "D", "E", "F", "G"]:
        for p in [
            f"{drive}:\\Program Files (x86)\\Steam",
            f"{drive}:\\Steam",
            f"{drive}:\\SteamLibrary",
        ]:
            path_obj = Path(p)
            if path_obj.exists():
                steam_paths.add(path_obj.resolve())

    # 3. Parse libraryfolders.vdf to discover additional multi-drive libraries
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

    Looks in the default ``wallpaper_engine/`` folder and, when the user
    renamed their Steam install folder, in the ``installdir`` recorded by
    ``appmanifest_431960.acf``.

    Args:
        lib_path: Root of a Steam library (directory containing ``steamapps/``).

    Returns:
        Paths to ``wallpaper32.exe`` / ``wallpaper64.exe`` that exist on disk.
        Empty when no install is present in this library.
    """
    common_dir = lib_path / "steamapps" / "common"
    found: list[Path] = []

    def _check_folder(folder: Path) -> None:
        for exe_name in _WE_EXE_NAMES:
            candidate = folder / exe_name
            if candidate.exists() and candidate not in found:
                found.append(candidate)

    _check_folder(common_dir / "wallpaper_engine")

    # Check via AppID manifest (431960) to handle custom folder names
    acf_path = lib_path / "steamapps" / "appmanifest_431960.acf"
    if acf_path.exists():
        try:
            acf_content = acf_path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r'"installdir"\s+"([^"]+)"', acf_content, re.IGNORECASE)
            if match:
                _check_folder(common_dir / match.group(1))
        except Exception:
            pass

    return found


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

    Returns:
        One pair per matching process, in snapshot order. Empty when none is
        running or the process snapshot cannot be created.
    """
    processes = get_running_processes()
    if processes is None:
        return []
    matches: list[tuple[int, Path]] = []
    for pid, name in processes:
        if name.lower() not in _WE_EXE_NAMES:
            continue
        path = get_executable_path(pid)
        if path is None:
            continue
        matches.append((pid, Path(path)))
    return matches


def close_process_by_pid(pid: int) -> None:
    """Post ``WM_CLOSE`` to every visible top-level window of ``pid``.

    Does not wait for exit; the target process decides whether to honor the
    close request.

    Args:
        pid: PID whose windows should receive the close request.
    """

    def enum_windows_callback(hwnd: int, extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
        if found_pid == pid:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    win32gui.EnumWindows(enum_windows_callback, None)


class WallpaperEngineApp(metaclass=SingletonMeta):
    """Control a Wallpaper Engine installation through its command line.

    Wraps the ``wallpaper32.exe`` / ``wallpaper64.exe`` CLI: locating the
    executable, normalizing its ``config.json``, and issuing
    ``-control <command> [flags]`` commands (e.g. ``-control play``,
    ``-control openWallpaper -file <path>``) to a running instance. Wallpaper
    Engine must already be running for a command to take effect.

    A process-wide singleton (see :class:`SingletonMeta`) — there is one
    installation and one ``config.json`` per machine. Call :meth:`start`
    before issuing any command.
    """

    def __init__(self, exe_path: Path | str | None = None) -> None:
        """Record the user-supplied exe path; resolution happens in :meth:`start`.

        Args:
            exe_path: Explicit ``wallpaper32.exe`` / ``wallpaper64.exe``
                path, or ``None`` to defer the choice to :meth:`start`.
        """
        self.exe_path: Path | None = Path(exe_path) if exe_path is not None else None
        self._original_monitor_detection: str | None = None

    def start(self) -> None:
        """Resolve the exe path, enforce monitor detection, and close any other
        running Wallpaper Engine instance so the new setting takes effect.

        When no Wallpaper Engine was running, registers
        :meth:`close_started_wallpaper_engine` so the instance this app causes to
        start is closed again at process exit.

        Raises:
            FileNotFoundError: No Wallpaper Engine exe was found and none was
                supplied, or the resolved exe's directory has no ``config.json``.
        """
        exe_path = self.exe_path
        matches = _find_running_wallpaper_engines()
        if exe_path is None:
            if matches:
                exe_path = matches[0][1]
            else:
                exes = resolve_wallpaper_engine_executables()
                if not exes:
                    raise FileNotFoundError(
                        "wallpaper engine executable (wallpaper32.exe / wallpaper64.exe) not found"
                    )
                exe_path = exes[0]
        self.exe_path = exe_path

        config_path = exe_path.parent / "config.json"
        if not config_path.exists():
            raise FileNotFoundError("wallpaper engine config file (config.json) not found")
        self.config_path: Path = config_path

        if self.enforce_monitor_detection_devicepath():
            atexit.register(self.restore_monitor_detection)

            chosen_norm = os.path.normpath(os.path.normcase(str(exe_path)))
            for pid, path in matches:
                if os.path.normpath(os.path.normcase(str(path))) == chosen_norm:
                    continue
                close_process_by_pid(pid)

        # atexit runs LIFO: registering this last makes it run *before*
        # restore_monitor_detection, so a closing Wallpaper Engine cannot
        # overwrite the restored monitordetection.
        if not matches:
            atexit.register(self.close_started_wallpaper_engine)

    def close_started_wallpaper_engine(self) -> None:
        """Close the Wallpaper Engine instance this app started.

        Registered with :mod:`atexit` by :meth:`start` only when no Wallpaper
        Engine was running at that point, so an instance the user launched is
        never closed. Running processes are re-scanned at exit and only those
        whose executable matches :attr:`exe_path`, compared case- and
        separator-insensitively, receive a close request.

        Does not wait for the process to exit; see :func:`close_process_by_pid`.
        """
        assert self.exe_path is not None
        chosen_norm = os.path.normpath(os.path.normcase(str(self.exe_path)))
        for pid, path in _find_running_wallpaper_engines():
            if os.path.normpath(os.path.normcase(str(path))) == chosen_norm:
                close_process_by_pid(pid)

    def apply_cli(self, cmd: list[str]) -> None:
        """Launch a Wallpaper Engine CLI command, detached from this process.

        Args:
            cmd: Full argv list, starting with the Wallpaper Engine executable.
        """
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )

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
            # Already correct: do not edit the file. Nothing to restore, nothing to restart.
            logger.info("wallpaper engine monitordetection: already 'devicepath', no change")
            return False
        logger.info("wallpaper engine monitordetection: %r -> 'devicepath'", original)
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
            logger.debug(
                "wallpaper engine monitordetection restore: no original captured, skipping"
            )
            return
        config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        user_section = config_data[getpass.getuser()]["general"]["user"]
        user_section["monitordetection"] = self._original_monitor_detection
        logger.info(
            "wallpaper engine monitordetection: restored to %r",
            self._original_monitor_detection,
        )
        self.config_path.write_text(
            json.dumps(config_data, indent="\t"),
            encoding="utf-8",
        )
        self._original_monitor_detection = None


class WallpaperEngineResource(BaseResource):
    """Wallpaper resource that delegates to a running Wallpaper Engine.

    The shared :class:`WallpaperEngineApp` is created lazily on the first
    :meth:`mount` and reused by every instance — Wallpaper Engine is a
    process-wide singleton with one install and one config file per machine.

    Args:
        path: Path to the wallpaper file (``.mp4`` video, ``scene.pkg``
            scene, or any format Wallpaper Engine understands).
    """

    #: Shared :class:`WallpaperEngineApp` instance. Lazily created on
    #: first :meth:`mount` so that simply constructing the resource does
    #: not require Wallpaper Engine to be installed.
    app: ClassVar[WallpaperEngineApp | None] = None

    def __init__(self, path: Path | str) -> None:
        super().__init__()
        self.image_path = Path(path)

    @classmethod
    def _ensure_app(cls) -> WallpaperEngineApp:
        """Return the shared :class:`WallpaperEngineApp`, starting it on first use.

        Returns:
            The shared :class:`WallpaperEngineApp` instance stored on
            :attr:`app`.
        """
        if cls.app is None:
            cls.app = WallpaperEngineApp()
            cls.app.start()
            logger.info(
                "wallpaper engine integration initialized: exe=%s",
                cls.app.exe_path,
            )
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

        No-op when the shared app was never initialized or no display is bound.
        """
        if WallpaperEngineResource.app is None or self.display is None:
            return
        WallpaperEngineResource.app.close_wallpaper(
            monitor=self.display.monitor_device_path,
        )
