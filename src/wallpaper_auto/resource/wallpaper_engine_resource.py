import getpass
import json
import logging
from pathlib import Path
import re
import subprocess
from typing import Optional
import winreg

from ..util.singleton_meta import SingletonMeta

logger = logging.getLogger(__name__)

# Sentinel meaning "the monitordetection key was absent in the original config".
# Distinct from None, which is a legitimate JSON value the user may have set.
_MISSING = object()


def get_steam_libraries() -> list[Path]:
    """Finds and returns a set of all Steam library Path objects on the system

    by checking the Windows registry, fallback drives, and libraryfolders.vdf.
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


def find_wallpaper_engine_executable_in_library(lib_path: Path) -> Path | None:
    """Searches for the Wallpaper Engine directory inside a single specified Steam library.

    Validates that wallpaper32.exe / wallpaper64.exe exists within the directory and returns
    the directory Path if found, or None otherwise.
    """
    common_dir = lib_path / "steamapps" / "common"

    # Check default installation folder name
    default_wp_dir = common_dir / "wallpaper_engine"
    if (default_wp_dir / "wallpaper32.exe").exists():
        return default_wp_dir

    # Check via AppID manifest (431960) to handle custom folder names
    acf_path = lib_path / "steamapps" / "appmanifest_431960.acf"
    if acf_path.exists():
        try:
            acf_content = acf_path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r'"installdir"\s+"([^"]+)"', acf_content, re.IGNORECASE)
            if match:
                folder_name = match.group(1)
                custom_32_exe_path = common_dir / folder_name / "wallpaper32.exe"
                if custom_32_exe_path.exists():
                    return custom_32_exe_path
                custom_64_exe_path = common_dir / folder_name / "wallpaper64.exe"
                if custom_64_exe_path.exists():
                    return custom_64_exe_path
        except Exception:
            pass

    return None


def resolve_wallpaper_engine_executable() -> Path | None:
    libraries = get_steam_libraries()    
    for library in libraries:
        result = find_wallpaper_engine_executable_in_library(library)
        if result:
            return result
    return None


class WallpaperEngineApp(metaclass=SingletonMeta):

    def __init__(self, exe_path: Path | str | None = None):
        if exe_path is None:
            exe_path = resolve_wallpaper_engine_executable()
            if exe_path is None:
                raise FileNotFoundError("wallpaper engine executablen (wallpaper32.exe / wallpaper64.exe) not found")
        self.exe_path: Path = Path(exe_path)
        config_path = self.exe_path.parent / "config.json"
        if config_path.exists() is False:
            raise FileNotFoundError("wallpaper engine config file (config.json) not found")
        self.config_path: Path = config_path
        self._original_monitor_detection: object = _MISSING

    def open_wallpaper(
        self,
        file: Path | str,
        location: str | None = None,
        monitor: int | None = None,
        play_in_window: str | None = None,
        width: int | None = None,
        height: int | None = None,
        x: int | None = None,
        y: int | None = None,
        preset: str | None = None,
        timeout: float = 10.0,
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
            timeout: Subprocess timeout in seconds.

        Raises:
            subprocess.CalledProcessError: Wallpaper Engine returned a non-zero
                exit code.
            subprocess.TimeoutExpired: The command did not complete within
                timeout seconds.
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
            ("-monitor", monitor),
            ("-playInWindow", play_in_window),
            ("-width", width),
            ("-height", height),
            ("-x", x),
            ("-y", y),
            ("-preset", preset),
        ):
            if value is not None:
                cmd.extend([flag, str(value)])
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True)

    def stop(self, timeout: float = 10.0) -> None:
        """Send the -control stop command to a running Wallpaper Engine instance.

        Args:
            timeout: Subprocess timeout in seconds.

        Raises:
            subprocess.CalledProcessError: Wallpaper Engine returned a non-zero
                exit code.
            subprocess.TimeoutExpired: The command did not complete within
                timeout seconds.
        """
        subprocess.run(
            [str(self.exe_path), "-control", "stop"],
            check=True,
            timeout=timeout,
            capture_output=True,
        )

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



