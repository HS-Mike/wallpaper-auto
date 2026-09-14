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

# Sentinel meaning "the monitordetection key was absent in the original config".
# Distinct from None, which is a legitimate JSON value the user may have set.
_MISSING = object()

#: Executable basenames Wallpaper Engine ships; checked in this order.
_WE_EXE_NAMES = ("wallpaper32.exe", "wallpaper64.exe")


def get_steam_libraries() -> list[Path]:
    """Find all Steam library directories on the system.

    Sources, in order: the Windows registry, common default installation
    locations across drives, and additional libraries listed in
    ``libraryfolders.vdf``.

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

    Checks both ``wallpaper32.exe`` and ``wallpaper64.exe`` inside the default
    ``wallpaper_engine/`` folder and inside the custom folder resolved via the
    Wallpaper Engine ``appmanifest_431960.acf`` ``installdir`` entry (when the
    user has renamed their Steam install folder). Returns all candidates that
    actually exist on disk; the caller decides which one to invoke.

    Args:
        lib_path: Root of a Steam library (directory containing ``steamapps/``).

    Returns:
        List of paths to ``wallpaper32.exe`` / ``wallpaper64.exe`` that exist
        on disk. Empty list if no install is present in this library.
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
        Every ``wallpaper32.exe`` / ``wallpaper64.exe`` path found, one entry
        per exe per Steam library. Empty when no install is present.
    """
    libraries = get_steam_libraries()
    all_exes: list[Path] = []
    for library in libraries:
        all_exes.extend(find_wallpaper_engine_executables_in_library(library))
    return all_exes


def _find_running_wallpaper_engines() -> list[tuple[int, Path]]:
    """Return ``(pid, exe_path)`` for every running Wallpaper Engine instance.

    Scans the live process list once for processes whose basename matches
    one of :data:`_WE_EXE_NAMES` (case-insensitive) and resolves each match
    to its full exe path via :func:`get_executable_path`.

    Returns:
        One ``(pid, exe_path)`` pair per matching process, in snapshot order.
        Empty when no matching process is running or the process snapshot
        cannot be created.
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

    Walks every top-level window via :func:`win32gui.EnumWindows` and,
    for each visible window owned by ``pid``, posts ``WM_CLOSE``. The
    target process decides whether to honor the close request; this
    function does not wait for exit.

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
    executable, normalizing its ``config.json``, and issuing ``-control``
    commands. Wallpaper Engine must already be running for a command to take
    effect.

    A process-wide singleton (see :class:`SingletonMeta`) — there is one
    installation and one ``config.json`` per machine. Call :meth:`start`
    before issuing any command.
    """

    def __init__(self, exe_path: Path | str | None = None) -> None:
        """Record the user-supplied exe path; resolution happens in :meth:`start`.

        When ``exe_path`` is provided it is stored verbatim on
        ``self.exe_path`` (no discovery, no process inspection). When it
        is ``None``, ``self.exe_path`` is left unset and :meth:`start` is
        responsible for picking one via running-process inspection or
        Steam-library discovery.

        Args:
            exe_path: Explicit ``wallpaper32.exe`` / ``wallpaper64.exe``
                path, or ``None`` to defer the choice to :meth:`start`.
        """
        self.exe_path: Path | None = Path(exe_path) if exe_path is not None else None
        self._original_monitor_detection: object = _MISSING

    def start(self) -> None:
        """Resolve ``self.exe_path``, enforce monitor detection, and clean up strays.

        Steps:

        1. If ``self.exe_path`` is ``None`` (the user did not pass one to
           :meth:`__init__`), pick one now: prefer a currently-running WE
           instance's full path; otherwise fall back to
           :func:`resolve_wallpaper_engine_executables` and take the first
           candidate. Raises ``FileNotFoundError`` if neither yields a path.
        2. Resolve ``self.config_path`` next to the exe, raising
           ``FileNotFoundError`` if ``config.json`` is absent.
        3. Force ``monitordetection`` to ``devicepath`` via
           :meth:`enforce_monitor_detection_devicepath` and register
           :meth:`restore_monitor_detection` with :mod:`atexit`, so the
           original value is put back on interpreter exit.
        4. Post ``WM_CLOSE`` to every running ``wallpaper32.exe`` /
           ``wallpaper64.exe`` whose normalized full path does not match
           ``self.exe_path``. Matching instances are preserved — the
           "use it directly" semantic.

        Raises:
            FileNotFoundError: No Wallpaper Engine exe was found AND the
                user did not supply one, OR the resolved exe's directory
                does not contain ``config.json``.
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

        self.enforce_monitor_detection_devicepath()
        atexit.register(self.restore_monitor_detection)

        chosen_norm = os.path.normpath(os.path.normcase(str(exe_path)))
        for pid, path in matches:
            if os.path.normpath(os.path.normcase(str(path))) == chosen_norm:
                continue
            close_process_by_pid(pid)

    def apply_cli(self, cmd: list[str]) -> None:
        """Launch a Wallpaper Engine CLI command, detached from this process.

        The subprocess is started with ``DETACHED_PROCESS | CREATE_NO_WINDOW``
        and its stdio discarded, then left to run — the command is
        fire-and-forget and its exit code is not collected.

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
        """Send the -control openWallpaper command to a running Wallpaper Engine instance.

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

    def play(self) -> None:
        """Send the -control play command to a running Wallpaper Engine instance.

        Resumes all wallpapers from a paused or stopped state.
        """
        cmd = [str(self.exe_path), "-control", "play"]
        self.apply_cli(cmd)

    def stop(self) -> None:
        """Send the -control stop command to a running Wallpaper Engine instance.

        Tells Wallpaper Engine to stop rendering the current wallpaper.
        The Wallpaper Engine process itself is left running.
        """
        cmd = [str(self.exe_path), "-control", "stop"]
        self.apply_cli(cmd)

    def close_wallpaper(
        self,
        location: str | None = None,
        monitor: str | int | None = None,
    ) -> None:
        """Send the -control closeWallpaper command to a running Wallpaper Engine instance.

        Removes a wallpaper from a specified monitor or window, or removes
        all wallpapers when neither flag is given (Wallpaper Engine's
        default behaviour).

        Args:
            location: Internal identifier of the monitor or window name from
                Wallpaper Engine's config.json. When None, the flag is
                omitted and Wallpaper Engine falls back to its default
                behaviour (remove all wallpapers).
            monitor: Index of the monitor to close the wallpaper from,
                beginning at 0. When None, the flag is omitted and Wallpaper
                Engine falls back to its default behaviour.
        """
        cmd: list[str] = [str(self.exe_path), "-control", "closeWallpaper"]
        for flag, value in (
            ("-location", location),
            ("-monitor", f'"{monitor}"' if isinstance(monitor, str) else monitor),
        ):
            if value is not None:
                cmd.extend([flag, str(value)])
        self.apply_cli(cmd)

    def enforce_monitor_detection_devicepath(self) -> None:
        """Ensure Wallpaper Engine's config.json has monitordetection set to "devicepath".

        Reads the current value under the current OS user's general.user.monitordetection, logs it,
        and overwrites it with "devicepath". The original value (or a sentinel if
        the key was absent) is saved on self._original_monitor_detection so
        restore_monitor_detection() can revert it.

        Raises:
            KeyError: The config.json does not contain the expected
                current OS user's general.user path (config has been edited by hand or by
                Wallpaper Engine itself into an unexpected shape).
            json.JSONDecodeError: The config.json is not valid JSON.
        """
        config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        user_section = config_data[getpass.getuser()]["general"]["user"]
        original = user_section.get("monitordetection", _MISSING)
        if original is _MISSING:
            logger.info("wallpaper engine monitordetection: <absent> -> 'devicepath'")
        else:
            logger.info("wallpaper engine monitordetection: %r -> 'devicepath'", original)
        user_section["monitordetection"] = "devicepath"
        self.config_path.write_text(
            json.dumps(config_data, indent="\t"),
            encoding="utf-8",
        )
        self._original_monitor_detection = original

    def restore_monitor_detection(self) -> None:
        """Restore the original monitordetection captured by enforce_monitor_detection_devicepath().

        No-op if enforce_monitor_detection_devicepath() was never called on this
        instance, or if it was already restored once. If the original value was
        the _MISSING sentinel (key absent), the key is removed from the JSON.

        Raises:
            KeyError: The config.json does not contain the expected
                current OS user's general.user path.
            json.JSONDecodeError: The config.json is not valid JSON.
        """
        if self._original_monitor_detection is _MISSING:
            logger.debug(
                "wallpaper engine monitordetection restore: no original captured, skipping"
            )
            return
        config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        user_section = config_data[getpass.getuser()]["general"]["user"]
        original = self._original_monitor_detection
        if original is _MISSING:
            user_section.pop("monitordetection", None)
            logger.info("wallpaper engine monitordetection: removed (was absent)")
        else:
            user_section["monitordetection"] = original
            logger.info("wallpaper engine monitordetection: restored to %r", original)
        self.config_path.write_text(
            json.dumps(config_data, indent="\t"),
            encoding="utf-8",
        )
        self._original_monitor_detection = _MISSING


class WallpaperEngineResource(BaseResource):
    """Wallpaper resource that delegates to a running Wallpaper Engine.

    The first time :meth:`mount` is called on any
    ``WallpaperEngineResource`` instance, a single
    :class:`WallpaperEngineApp` is instantiated and stashed in the class
    attribute :attr:`app`. All subsequent mount calls (on this or any
    other instance) reuse that same instance. This matches the
    process-singleton nature of Wallpaper Engine — there is one
    installation per machine and one config file.

    The target monitor is taken from ``self.display.monitor_device_path``
    (set by :meth:`_bind_display`) and passed to Wallpaper Engine's CLI as
    the ``-monitor`` selector.

    Args:
        path: Path to the wallpaper file (``.mp4`` video, ``scene.pkg``
            scene, or any format Wallpaper Engine understands).

    Note:
        :meth:`demount` closes the wallpaper on this resource's bound
        monitor via Wallpaper Engine's ``-control closeWallpaper``
        command, scoped by ``-monitor`` so wallpapers on other monitors
        are unaffected. If the resource was never mounted (``app`` is
        still None), or no display is bound, :meth:`demount` is a no-op.
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
        """Lazily instantiate the shared :class:`WallpaperEngineApp`.

        On first call, creates the singleton and runs
        :meth:`WallpaperEngineApp.start` to resolve the exe path, force
        ``monitordetection`` to ``devicepath``, and close any other running
        Wallpaper Engine instances; the resolved exe path is then logged.
        Subsequent calls return the already-initialised instance without
        re-running any of those steps.

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
        """Open the wallpaper on the bound display and resume playback.

        Lazily initialises the shared :class:`WallpaperEngineApp` on first
        call, then sends ``openWallpaper`` scoped to the display's
        ``monitor_device_path`` followed by a ``play`` command.
        """
        app = WallpaperEngineResource._ensure_app()
        assert self.display is not None
        app.open_wallpaper(
            file=self.image_path,
            monitor=self.display.monitor_device_path,
        )
        app.play()

    def demount(self) -> None:
        """Close the wallpaper on the bound display, leaving other monitors alone.

        No-op when the shared app was never initialised or no display is bound.
        """
        if WallpaperEngineResource.app is None or self.display is None:
            return
        WallpaperEngineResource.app.close_wallpaper(
            monitor=self.display.monitor_device_path,
        )
