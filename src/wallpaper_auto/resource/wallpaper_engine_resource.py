import atexit
import getpass
import json
import logging
import re
import subprocess
import time
import winreg
from pathlib import Path
from typing import ClassVar

import win32con
import win32event
import win32gui
import win32process

from ..util.process_util import get_executable_path, get_running_processes
from ..util.singleton_meta import SingletonMeta
from .base_resource import BaseResource

logger = logging.getLogger(__name__)

#: Executable basenames Wallpaper Engine ships; checked in this order.
_WE_EXE_NAMES = ("wallpaper32.exe", "wallpaper64.exe")

#: Seconds to wait for a Wallpaper Engine CLI client to return before giving up.
_WE_CLI_TIMEOUT_SECONDS = 10.0

#: Seconds to wait for a running Wallpaper Engine to exit after a close request.
#: Bounded so neither :meth:`WallpaperEngineApp.start` nor
#: :meth:`WallpaperEngineApp.stop` can hang.
_WE_EXIT_TIMEOUT_SECONDS = 5.0

#: Seconds between close requests while waiting for that exit. Wallpaper Engine's
#: windows do not exist for the first ~300ms of its life, and a close request
#: posted before then has no window to go to and is lost, so it must be retried.
_WE_CLOSE_RETRY_SECONDS = 0.25

#: Seconds to wait for the two-candidate race in :meth:`WallpaperEngineApp._resolve_host_race`
#: to leave a single host. The loser exits ~0.1s after the winner takes the
#: single-instance slot (measured on a cold start); the budget covers a slower one.
_WE_HOST_RACE_TIMEOUT_SECONDS = 3.0

#: Seconds to keep waiting, after one candidate has exited, for the survivor to
#: exit as well. A forwarder exits as soon as it finds a host, so of two
#: simultaneous candidates the survivor is a forwarder too only when an instance
#: this app did not start already owns the desktop — and then it exits about as
#: fast as the first did. A host never exits, so this window tells them apart.
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
    """Post ``WM_CLOSE`` to every top-level window of ``pid``.

    Hidden windows are included on purpose: the only window Wallpaper Engine
    honors the close request on is its hidden ``WPEEventWindow``. Its visible
    window and ``WPETrayWindow`` ignore it, and a running instance started
    without its UI has no visible window at all.

    Does not wait for exit; the target process decides whether to honor the
    close request.

    Args:
        pid: PID whose windows should receive the close request.
    """

    def enum_windows_callback(hwnd: int, extra: object) -> None:
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
        self._popen: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Resolve the exe path, enforce monitor detection, then restart Wallpaper
        Engine and hold the new instance.

        Every running instance is closed, unconditionally, and two are launched
        with ``-silent`` (no main window) and raced against each other; the one
        that takes the single-instance slot is observed and kept on
        :attr:`_popen`, so :meth:`stop` closes exactly that process.
        ``monitordetection`` only takes effect on the next launch, and a launch
        while an instance is still alive would only forward to it and exit, so
        the instances go first and the new one starts once none is left.

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

        self.enforce_monitor_detection_devicepath()

        # A changed monitordetection only takes effect on the next launch, and a
        # spawn while an instance is still alive would just forward to it and
        # exit, so every instance is closed first and the new one is started only
        # once none is left.
        self._popen = None
        remaining = matches
        deadline = time.monotonic() + _WE_EXIT_TIMEOUT_SECONDS
        while remaining:
            for pid, _ in remaining:
                close_process_by_pid(pid)
            if time.monotonic() >= deadline:
                logger.warning(
                    "wallpaper engine: %d instance(s) did not close within %ss",
                    len(remaining),
                    _WE_EXIT_TIMEOUT_SECONDS,
                )
                break
            time.sleep(_WE_CLOSE_RETRY_SECONDS)
            remaining = _find_running_wallpaper_engines()

        self._ensure_instance()

    def _ensure_instance(self) -> None:
        """Start and hold a Wallpaper Engine instance when none is running.

        The only place that can bring a new instance into existence. An instance
        already running — ours, or one the user launched — is reused and never
        owned.

        Wallpaper Engine's ``-control`` commands require an already running
        instance, so this is a precondition of every command.

        ``-silent`` is undocumented but real — it is the flag Wallpaper Engine's
        own ``Run`` autostart entry uses — and starts it without a main window.
        """
        if self._popen is not None and self._popen.poll() is None:
            return
        self._popen = None
        if _find_running_wallpaper_engines():
            logger.info("wallpaper engine: an instance is already running, not claimed")
            return
        exe_path = self.exe_path
        assert exe_path is not None
        # Two launches race for the single-instance slot. The winner becomes the
        # host; the loser finds it, forwards and exits, so the loser's exit is
        # itself the proof that the host is reachable — which is what a
        # `-control` client needs before it can hand a command over. `-silent`
        # carries no command, so the losing candidate has no effect on playback.
        candidates = [
            subprocess.Popen(
                [str(exe_path), "-silent"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS,
            )
            for _ in range(2)
        ]
        self._popen = self._resolve_host_race(candidates)

    def _resolve_host_race(
        self, candidates: list[subprocess.Popen[bytes]]
    ) -> subprocess.Popen[bytes] | None:
        """Return the candidate that became the host, or ``None``.

        Wallpaper Engine is single-instance, so of two simultaneous launches one
        takes the slot and lives on while the other reaches it, forwards and
        exits. That exit is the signal: it proves the survivor is the host and is
        reachable, which is what :meth:`apply_cli` depends on.

        Args:
            candidates: The simultaneously spawned ``-silent`` processes.

        Returns:
            The surviving host, or ``None`` when every candidate exited — they
            all reached an instance that was already running.
        """
        # Waiting on the process handles returns the moment a candidate dies
        # instead of discovering it on a poll, and reports which one it was.
        # ``_handle`` is private but stable for the ``Popen``'s lifetime on
        # Windows (``_internal_poll`` never closes it) and a process handle stays
        # signaled once its process has exited, so this wait is exact. An
        # unusable handle raises out of pywin32; that is not recoverable, since
        # every handle here belongs to a process this method just spawned.
        handles = [p._handle for p in candidates]  # type: ignore[attr-defined]
        result = win32event.WaitForMultipleObjects(
            handles,
            False,
            int(_WE_HOST_RACE_TIMEOUT_SECONDS * 1000),
        )
        if result == win32event.WAIT_TIMEOUT:
            # No candidate yielded, so the race cannot tell the host from a
            # straggler stuck waiting for it. Hold the first and close the rest:
            # every candidate is a process this app started, so none of them may
            # outlive us unowned.
            for extra in candidates[1:]:
                logger.warning("wallpaper engine: closing redundant instance %s", extra.pid)
                close_process_by_pid(extra.pid)
            logger.warning(
                "wallpaper engine: race unresolved after %ss, holding %s",
                _WE_HOST_RACE_TIMEOUT_SECONDS,
                candidates[0].pid,
            )
            return candidates[0]
        # The wait reports the lowest-indexed candidate that exited; with two
        # candidates the other one is the survivor. An immediate liveness check
        # is not enough to call it a host, because a second forwarder exits a
        # moment after the first does — so it gets its own short wait. Only a
        # process that stays alive through both is a host.
        exited = result - win32event.WAIT_OBJECT_0
        survivor_index = next(i for i in range(len(candidates)) if i != exited)
        if (
            win32event.WaitForMultipleObjects(
                [handles[survivor_index]],
                False,
                int(_WE_HOST_SETTLE_SECONDS * 1000),
            )
            != win32event.WAIT_TIMEOUT
        ):
            logger.warning(
                "wallpaper engine: every spawned candidate exited; another "
                "instance owns the desktop"
            )
            return None
        survivor = candidates[survivor_index]
        logger.info("wallpaper engine: holding host process %s", survivor.pid)
        return survivor

    def stop(self) -> None:
        """Close the instance this app started, then restore ``monitordetection``.

        Only the process launched by :meth:`_ensure_instance` is closed; an
        instance that was already running, or one the user launched, is left
        alone. The close request is retried every :data:`_WE_CLOSE_RETRY_SECONDS`
        until the process exits or the :data:`_WE_EXIT_TIMEOUT_SECONDS` budget is
        spent — the request is lost when it has no window to go to yet, which is
        the normal case for an instance started moments ago. Waiting on that exit
        lets it flush ``config.json`` before ``monitordetection`` is written back.

        Safe to call more than once, and before :meth:`start`.
        """
        popen, self._popen = self._popen, None
        if popen is not None:
            deadline = time.monotonic() + _WE_EXIT_TIMEOUT_SECONDS
            while popen.poll() is None:
                close_process_by_pid(popen.pid)
                try:
                    popen.wait(timeout=_WE_CLOSE_RETRY_SECONDS)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        logger.warning(
                            "wallpaper engine process %s did not exit within %ss",
                            popen.pid,
                            _WE_EXIT_TIMEOUT_SECONDS,
                        )
                        break
        # Restored even when nothing was owned: a foreign host is then still
        # alive while its config.json is written back. Deliberate — our edit has
        # to be reverted either way, and leaving it in place would be worse.
        self.restore_monitor_detection()

    def apply_cli(self, cmd: list[str]) -> None:
        """Run a Wallpaper Engine CLI command, waiting on it only up to a timeout.

        Args:
            cmd: Full argv list, starting with the Wallpaper Engine executable.
        """
        self._ensure_instance()
        logger.debug("wallpaper engine cli: %s", cmd)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS,
        )
        try:
            process.wait(timeout=_WE_CLI_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                "wallpaper engine cli did not return within %ss (pid=%s): %s",
                _WE_CLI_TIMEOUT_SECONDS,
                process.pid,
                cmd,
            )
            held = self._popen
            if held is None or held.poll() is not None:
                # The command started the instance itself: hold it so stop()
                # can close it instead of leaking an unowned process.
                self._popen = process

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
            raise RuntimeError()
        WallpaperEngineResource.app.close_wallpaper(
            monitor=self.display.monitor_device_path,
        )
