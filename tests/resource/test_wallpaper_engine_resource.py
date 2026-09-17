"""Tests for wallpaper_engine_resource.py — Steam discovery and the Wallpaper Engine lifecycle."""

import getpass
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, call

import pytest
import win32event

from wallpaper_auto.resource import wallpaper_engine_resource as we_mod
from wallpaper_auto.resource.wallpaper_engine_resource import (
    WallpaperEngineApp,
    WallpaperEngineResource,
    _find_running_wallpaper_engines,
    find_wallpaper_engine_executables_in_library,
    get_steam_libraries,
    resolve_wallpaper_engine_executables,
)
from wallpaper_auto.util.display_util import LUID, DisplayInfo

_DEVICE_PATH = r"\\?\DISPLAY#TEST#{test-device}"
_EXE_PATH = Path(r"C:\Steam\steamapps\common\wallpaper_engine\wallpaper32.exe")
_OMIT = object()

_APPIMAGE = '"AppState"\n{{\n\t"installdir"\t\t"{folder}"\n}}'


class _FakePopen:
    """Stand-in exposing the ``Popen`` surface the module touches."""

    def __init__(
        self,
        pid: int,
        handle: int,
        exit_code: int | None = None,
        wait_error: Exception | None = None,
    ) -> None:
        self.pid = pid
        self._handle = handle
        self._exit_code = exit_code
        self.wait = MagicMock(side_effect=wait_error)

    def poll(self) -> int | None:
        return self._exit_code


def _stub_atexit(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the module's ``atexit`` with a mock and return it.

    The real ``atexit.register`` breaks pytest-qt, which registers callbacks
    taking extra arguments.
    """
    stub = MagicMock()
    monkeypatch.setattr(we_mod, "atexit", stub)
    return stub


@pytest.fixture(autouse=True)
def _reset_we_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Free the shared app and stub ``atexit`` before each test."""
    _stub_atexit(monkeypatch)
    WallpaperEngineApp.clear_instance()
    WallpaperEngineResource.app = None
    yield
    WallpaperEngineApp.clear_instance()
    WallpaperEngineResource.app = None


def _make_display() -> DisplayInfo:
    """DisplayInfo with a stable identity for resource binding."""
    return DisplayInfo(
        device_name="\\\\.\\DISPLAY1",
        model="U2719D",
        source_resolution=(1920, 1080),
        position=(0, 0),
        target_resolution=(1920, 1080),
        adapter_id=LUID(1, 2),
        source_id=3,
        scale=100,
        monitor_device_path=_DEVICE_PATH,
    )


def _write_user_config(config_path: Path, user_section: object) -> None:
    """Write a config.json holding the current user's ``general.user`` section."""
    config_path.write_text(
        json.dumps({getpass.getuser(): {"general": {"user": user_section}}}),
        encoding="utf-8",
    )


def _read_user_section(config_path: Path) -> dict[str, object]:
    """Return the current user's ``general.user`` section from a config.json."""
    return json.loads(config_path.read_text(encoding="utf-8"))[getpass.getuser()]["general"]["user"]


def _make_exe_dir(directory: Path, monitordetection: object = "monitorindex") -> Path:
    """Create a fake Wallpaper Engine install and return its exe path.

    Args:
        directory: Directory to hold the fake install.
        monitordetection: Value for the config's ``monitordetection``; pass
            ``_OMIT`` to leave the key out.
    """
    directory.mkdir(parents=True, exist_ok=True)
    user_section: dict[str, object] = {}
    if monitordetection is not _OMIT:
        user_section["monitordetection"] = monitordetection
    (directory / "wallpaper32.exe").write_bytes(b"")
    _write_user_config(directory / "config.json", user_section)
    return directory / "wallpaper32.exe"


def _spy_app() -> tuple[WallpaperEngineApp, MagicMock]:
    """Return an app whose ``apply_cli`` is a spy, so no process is ever launched."""
    app = WallpaperEngineApp(_EXE_PATH)
    apply_cli = MagicMock()
    app.apply_cli = apply_cli
    return app, apply_cli


def _init_library(library: Path) -> Path:
    """Create ``steamapps/common`` under ``library`` so it looks like a Steam library root."""
    (library / "steamapps" / "common").mkdir(parents=True)
    return library


def _install_exe(library: Path, folder: str, *names: str) -> list[Path]:
    """Drop the named exes into ``steamapps/common/<folder>`` and return their paths."""
    install = library / "steamapps" / "common" / folder
    install.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        exe = install / name
        exe.write_bytes(b"")
        paths.append(exe)
    return paths


def _write_appmanifest(library: Path, content: str) -> None:
    """Write ``appmanifest_431960.acf`` into the library's ``steamapps/``."""
    (library / "steamapps" / "appmanifest_431960.acf").write_text(content, encoding="utf-8")


class TestGetSteamLibraries:
    """Steam library roots from the registry, default drive directories and libraryfolders.vdf."""

    @staticmethod
    def _patch_sources(
        monkeypatch: pytest.MonkeyPatch,
        registry_install: Path | None = None,
        drives: list[str] | None = None,
    ) -> None:
        """Patch the registry and drive enumeration the discovery walks."""
        fake_winreg = MagicMock()
        if registry_install is None:
            fake_winreg.OpenKey.side_effect = OSError
        else:
            fake_winreg.QueryValueEx.return_value = (str(registry_install), 1)
        fake_os = MagicMock()
        fake_os.listdrives.return_value = drives or []
        monkeypatch.setattr(we_mod, "winreg", fake_winreg)
        monkeypatch.setattr(we_mod, "os", fake_os)

    def test_registry_install_path_is_included(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steam = tmp_path / "Steam"
        steam.mkdir()
        self._patch_sources(monkeypatch, registry_install=steam)

        assert get_steam_libraries() == [steam.resolve()]

    def test_default_drive_directories_are_searched_when_the_registry_lookup_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steam = tmp_path / "Steam"
        steam.mkdir()
        self._patch_sources(monkeypatch, drives=[f"{tmp_path}\\\\"])

        assert get_steam_libraries() == [steam.resolve()]

    def test_registry_install_path_that_does_not_exist_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_sources(monkeypatch, registry_install=tmp_path / "missing")

        assert get_steam_libraries() == []

    def test_libraryfolders_vdf_paths_are_included(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steam = tmp_path / "Steam"
        (steam / "steamapps").mkdir(parents=True)
        extra = tmp_path / "ExtraLibrary"
        extra.mkdir()
        escaped = str(extra).replace("\\", "\\\\")
        (steam / "steamapps" / "libraryfolders.vdf").write_text(
            f'"libraryfolders"\n{{\n\t"1"\n\t{{\n\t\t"path"\t\t"{escaped}"\n\t}}\n}}',
            encoding="utf-8",
        )
        self._patch_sources(monkeypatch, registry_install=steam)

        assert set(get_steam_libraries()) == {steam.resolve(), extra.resolve()}

    def test_unreadable_libraryfolders_vdf_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steam = tmp_path / "Steam"
        steam.mkdir()
        # a directory is an existing path whose read_text() fails: present but unreadable
        (steam / "steamapps" / "libraryfolders.vdf").mkdir(parents=True)
        self._patch_sources(monkeypatch, registry_install=steam)

        assert get_steam_libraries() == [steam.resolve()]

    def test_duplicate_libraries_are_returned_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steam = tmp_path / "Steam"
        steam.mkdir()
        self._patch_sources(monkeypatch, registry_install=steam, drives=[f"{tmp_path}\\\\"])

        assert get_steam_libraries() == [steam.resolve()]

    def test_no_steam_install_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_sources(monkeypatch, drives=[f"{tmp_path}\\\\"])

        assert get_steam_libraries() == []


class TestFindWallpaperEngineExecutablesInLibrary:
    """Executable discovery inside a single Steam library."""

    def test_finds_both_executables_in_the_default_install_folder(self, tmp_path: Path) -> None:
        library = _init_library(tmp_path)
        expected = _install_exe(library, "wallpaper_engine", "wallpaper32.exe", "wallpaper64.exe")

        assert set(find_wallpaper_engine_executables_in_library(library)) == set(expected)

    def test_finds_executables_in_the_appmanifest_installdir_folder(self, tmp_path: Path) -> None:
        library = _init_library(tmp_path)
        expected = _install_exe(library, "custom", "wallpaper64.exe")
        _write_appmanifest(library, _APPIMAGE.format(folder="custom"))

        assert find_wallpaper_engine_executables_in_library(library) == expected

    def test_appmanifest_without_an_installdir_finds_nothing(self, tmp_path: Path) -> None:
        library = _init_library(tmp_path)
        _write_appmanifest(library, '"AppState"\n{\n}')

        assert find_wallpaper_engine_executables_in_library(library) == []

    def test_unreadable_appmanifest_is_skipped(self, tmp_path: Path) -> None:
        library = _init_library(tmp_path)
        expected = _install_exe(library, "wallpaper_engine", "wallpaper32.exe")
        # a directory is an existing path whose read_text() fails: present but unreadable
        (library / "steamapps" / "appmanifest_431960.acf").mkdir()

        assert find_wallpaper_engine_executables_in_library(library) == expected

    def test_executables_in_both_folders_are_returned_once(self, tmp_path: Path) -> None:
        library = _init_library(tmp_path)
        expected = _install_exe(library, "wallpaper_engine", "wallpaper32.exe")
        _write_appmanifest(library, _APPIMAGE.format(folder="wallpaper_engine"))

        assert find_wallpaper_engine_executables_in_library(library) == expected


class TestResolveWallpaperEngineExecutables:
    """Fan-out of executable discovery across every discovered library."""

    def test_aggregates_executables_from_every_library(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = _install_exe(_init_library(tmp_path / "a"), "wallpaper_engine", "wallpaper32.exe")
        second = _install_exe(_init_library(tmp_path / "b"), "wallpaper_engine", "wallpaper64.exe")
        monkeypatch.setattr(
            we_mod,
            "get_steam_libraries",
            MagicMock(return_value=[tmp_path / "a", tmp_path / "b"]),
        )

        assert set(resolve_wallpaper_engine_executables()) == {first[0], second[0]}


class TestFindRunningWallpaperEngines:
    """Which running processes count as Wallpaper Engine instances."""

    @staticmethod
    def _patch_scan(
        monkeypatch: pytest.MonkeyPatch,
        snapshot: list[tuple[int, str]],
        library_executables: list[Path],
        path_by_pid: dict[int, Path | None],
    ) -> MagicMock:
        """Patch the process snapshot, library scan and path resolution; return the path lookup.

        Args:
            monkeypatch: Fixture used to install the three patches.
            snapshot: Return value for ``get_running_processes``, which yields
                ``(pid, exe basename)`` pairs. The name is only the cheap
                prefilter; a pid's identity comes from the resolved path below,
                which is why this is a ``str`` while every other path here is a
                ``Path``.
            library_executables: Return value for
                ``resolve_wallpaper_engine_executables`` — the exes that count
                as installed Wallpaper Engine.
            path_by_pid: Lookup table for ``get_executable_path``, keyed by pid;
                ``None`` models a pid whose path could not be resolved.

        Returns:
            The ``get_executable_path`` mock, so a test can assert it was never
            called for a process that failed the prefilter.
        """
        monkeypatch.setattr(we_mod, "get_running_processes", MagicMock(return_value=snapshot))
        monkeypatch.setattr(
            we_mod,
            "resolve_wallpaper_engine_executables",
            MagicMock(return_value=library_executables),
        )
        path_lookup = MagicMock(side_effect=path_by_pid.get)
        monkeypatch.setattr(we_mod, "get_executable_path", path_lookup)
        return path_lookup

    def test_matching_process_is_returned_with_its_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_scan(monkeypatch, [(100, "wallpaper32.exe")], [_EXE_PATH], {100: _EXE_PATH})

        assert _find_running_wallpaper_engines() == [(100, _EXE_PATH)]

    def test_process_with_a_different_name_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_lookup = self._patch_scan(
            monkeypatch, [(100, "notepad.exe")], [_EXE_PATH], {100: _EXE_PATH}
        )

        assert _find_running_wallpaper_engines() == []
        path_lookup.assert_not_called()

    def test_process_with_an_unresolvable_path_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_scan(monkeypatch, [(100, "wallpaper32.exe")], [_EXE_PATH], {100: None})

        assert _find_running_wallpaper_engines() == []

    def test_process_outside_a_discovered_library_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        foreign = Path(r"D:\Elsewhere\wallpaper32.exe")
        self._patch_scan(monkeypatch, [(100, "wallpaper32.exe")], [_EXE_PATH], {100: foreign})

        assert _find_running_wallpaper_engines() == []

    def test_name_match_ignores_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_scan(monkeypatch, [(100, "Wallpaper64.EXE")], [_EXE_PATH], {100: _EXE_PATH})

        assert _find_running_wallpaper_engines() == [(100, _EXE_PATH)]


class TestWallpaperEngineAppStart:
    """Executable resolution, the config guard, and instance replacement in ``start()``."""

    @staticmethod
    def _prepare(
        monkeypatch: pytest.MonkeyPatch,
        exe_path: Path | None = None,
        running: list[tuple[int, Path]] | None = None,
        discovered: list[Path] | None = None,
    ) -> tuple[WallpaperEngineApp, _FakePopen, SimpleNamespace]:
        """Patch the machine-state seams and return the app, its stubbed host and the seams."""
        find_running = MagicMock(return_value=running or [])
        close = MagicMock()
        resolve = MagicMock(return_value=discovered or [])
        monkeypatch.setattr(we_mod, "_find_running_wallpaper_engines", find_running)
        monkeypatch.setattr(we_mod, "close_and_wait_for_exit", close)
        monkeypatch.setattr(we_mod, "resolve_wallpaper_engine_executables", resolve)
        app = WallpaperEngineApp(exe_path)
        host = _FakePopen(777, 0xA0)
        host_stub = MagicMock(return_value=host)
        app._start_host = host_stub
        return (
            app,
            host,
            SimpleNamespace(
                find_running=find_running, close=close, resolve=resolve, host=host_stub
            ),
        )

    def test_uses_the_supplied_executable_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = _make_exe_dir(tmp_path)
        app, host, seams = self._prepare(monkeypatch, exe_path=exe)

        app.start()

        assert app.exe_path == exe
        assert app.config_path == exe.parent / "config.json"
        assert app._popen is host
        seams.resolve.assert_not_called()

    def test_uses_the_running_instance_executable_when_none_is_supplied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = _make_exe_dir(tmp_path)
        app, _, seams = self._prepare(monkeypatch, running=[(11, exe)])

        app.start()

        assert app.exe_path == exe
        seams.resolve.assert_not_called()

    def test_falls_back_to_a_discovered_executable_when_none_is_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = _make_exe_dir(tmp_path)
        app, _, _ = self._prepare(monkeypatch, discovered=[exe])

        app.start()

        assert app.exe_path == exe

    def test_raises_when_no_executable_is_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _, seams = self._prepare(monkeypatch)

        with pytest.raises(FileNotFoundError, match="not found"):
            app.start()
        seams.host.assert_not_called()

    def test_raises_when_config_json_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = tmp_path / "wallpaper32.exe"
        exe.write_bytes(b"")
        app, _, seams = self._prepare(monkeypatch, exe_path=exe)

        with pytest.raises(FileNotFoundError, match="config.json"):
            app.start()
        seams.close.assert_not_called()
        seams.host.assert_not_called()

    def test_closes_every_instance_from_the_entry_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = _make_exe_dir(tmp_path)
        app, _, seams = self._prepare(monkeypatch, exe_path=exe, running=[(11, exe), (22, exe)])

        app.start()

        seams.close.assert_called_once_with([11, 22])
        assert seams.find_running.call_count == 1

    def test_enforces_monitor_detection_then_closes_then_starts_the_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = _make_exe_dir(tmp_path)
        app, _, seams = self._prepare(monkeypatch, exe_path=exe)
        order: list[str] = []
        app.enforce_monitor_detection_devicepath = MagicMock(
            side_effect=lambda: order.append("enforce") or False
        )
        seams.close.side_effect = lambda pids: order.append("close")
        app._start_host = MagicMock(
            side_effect=lambda: (order.append("host"), _FakePopen(777, 0xA0))[1]
        )

        app.start()

        assert order == ["enforce", "close", "host"]

    def test_malformed_config_raises_before_any_instance_is_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exe = _make_exe_dir(tmp_path, monitordetection=_OMIT)
        app, _, seams = self._prepare(monkeypatch, exe_path=exe, running=[(11, exe)])

        with pytest.raises(KeyError):
            app.start()
        seams.close.assert_not_called()
        seams.host.assert_not_called()


class TestWallpaperEngineStartHost:
    """The two-candidate host race and its three outcomes."""

    @staticmethod
    def _prepare(
        monkeypatch: pytest.MonkeyPatch, candidates: list[_FakePopen], waits: list[int]
    ) -> tuple[WallpaperEngineApp, SimpleNamespace]:
        """Patch the spawn, wait and close seams; return an app and the seams."""
        spawn = MagicMock(side_effect=candidates)
        wait = MagicMock(side_effect=waits)
        close = MagicMock()
        monkeypatch.setattr(we_mod.subprocess, "Popen", spawn)
        monkeypatch.setattr(we_mod.win32event, "WaitForMultipleObjects", wait)
        monkeypatch.setattr(we_mod, "post_close_message", close)
        return WallpaperEngineApp(_EXE_PATH), SimpleNamespace(spawn=spawn, wait=wait, close=close)

    def test_spawns_two_silent_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, seams = self._prepare(
            monkeypatch,
            [_FakePopen(1000, 0xA0), _FakePopen(1001, 0xA1)],
            [win32event.WAIT_OBJECT_0, win32event.WAIT_TIMEOUT],
        )

        app._start_host()

        spawn = call(
            [str(_EXE_PATH), "-silent"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS,
        )
        assert seams.spawn.call_args_list == [spawn, spawn]

    def test_returns_the_survivor_when_the_first_candidate_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cand0, cand1 = _FakePopen(1000, 0xA0), _FakePopen(1001, 0xA1)
        app, seams = self._prepare(
            monkeypatch, [cand0, cand1], [win32event.WAIT_OBJECT_0, win32event.WAIT_TIMEOUT]
        )

        assert app._start_host() is cand1
        assert seams.wait.call_args_list == [
            call([0xA0, 0xA1], False, 3000),
            call([0xA1], False, 250),
        ]

    def test_returns_the_survivor_when_the_second_candidate_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cand0, cand1 = _FakePopen(1000, 0xA0), _FakePopen(1001, 0xA1)
        app, _ = self._prepare(
            monkeypatch,
            [cand0, cand1],
            [win32event.WAIT_OBJECT_0 + 1, win32event.WAIT_TIMEOUT],
        )

        assert app._start_host() is cand0

    def test_does_not_touch_the_held_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        held = _FakePopen(555, 0xAA)
        app, _ = self._prepare(
            monkeypatch,
            [_FakePopen(1000, 0xA0), _FakePopen(1001, 0xA1)],
            [win32event.WAIT_OBJECT_0, win32event.WAIT_TIMEOUT],
        )
        app._popen = cast(subprocess.Popen[bytes], held)

        app._start_host()

        assert app._popen is held

    def test_raises_when_neither_candidate_exits_in_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, seams = self._prepare(
            monkeypatch,
            [_FakePopen(1000, 0xA0), _FakePopen(1001, 0xA1)],
            [win32event.WAIT_TIMEOUT],
        )

        with pytest.raises(RuntimeError, match="host race timeout"):
            app._start_host()

        assert seams.wait.call_count == 1
        assert [each.args[0] for each in seams.close.call_args_list] == [1000, 1001]

    def test_raises_when_the_survivor_also_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _ = self._prepare(
            monkeypatch,
            [_FakePopen(1000, 0xA0), _FakePopen(1001, 0xA1)],
            [win32event.WAIT_OBJECT_0, win32event.WAIT_OBJECT_0],
        )

        with pytest.raises(RuntimeError, match="failed to settle"):
            app._start_host()

    def test_cleanup_requests_close_only_for_still_running_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, seams = self._prepare(
            monkeypatch,
            [_FakePopen(1000, 0xA0, exit_code=0), _FakePopen(1001, 0xA1)],
            [win32event.WAIT_OBJECT_0, win32event.WAIT_OBJECT_0],
        )

        with pytest.raises(RuntimeError):
            app._start_host()

        assert [each.args[0] for each in seams.close.call_args_list] == [1001]


class TestWallpaperEngineAppStop:
    """Closing the owned instance and the unconditional monitor-detection restore."""

    def test_closes_the_owned_process_and_forgets_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        close = MagicMock()
        monkeypatch.setattr(we_mod, "close_and_wait_for_exit", close)
        app = WallpaperEngineApp(_EXE_PATH)
        app._popen = cast(subprocess.Popen[bytes], _FakePopen(777, 0xAA))

        app.stop()

        close.assert_called_once_with([777])
        assert app._popen is None

    def test_restores_monitor_detection_when_it_owned_no_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        close = MagicMock()
        monkeypatch.setattr(we_mod, "close_and_wait_for_exit", close)
        app = WallpaperEngineApp(_EXE_PATH)
        app.restore_monitor_detection = MagicMock()

        app.stop()

        close.assert_not_called()
        app.restore_monitor_detection.assert_called_once_with()

    def test_restore_runs_after_the_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        order: list[str] = []
        close = MagicMock(side_effect=lambda pids: order.append("close"))
        monkeypatch.setattr(we_mod, "close_and_wait_for_exit", close)
        app = WallpaperEngineApp(_EXE_PATH)
        app._popen = cast(subprocess.Popen[bytes], _FakePopen(777, 0xAA))
        app.restore_monitor_detection = MagicMock(side_effect=lambda: order.append("restore"))

        app.stop()

        assert order == ["close", "restore"]


class TestApplyCli:
    """Waiting on a CLI client, and raising when it outlives the timeout."""

    @staticmethod
    def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakePopen) -> MagicMock:
        """Patch the spawn so ``apply_cli`` gets ``client``; return the spawn mock."""
        spawn = MagicMock(return_value=client)
        monkeypatch.setattr(we_mod.subprocess, "Popen", spawn)
        return spawn

    @staticmethod
    def _timeout() -> subprocess.TimeoutExpired:
        """A timeout error matching the module's 10s client budget."""
        return subprocess.TimeoutExpired("cmd", 10.0)

    def test_waits_for_the_client_with_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A client that returns in time is spawned detached and waited on, never adopted."""
        client = _FakePopen(900, 0xB0)
        spawn = self._patch_client(monkeypatch, client)
        app = WallpaperEngineApp(_EXE_PATH)

        app.apply_cli(["cmd"])

        client.wait.assert_called_once_with(timeout=10.0)
        spawn.assert_called_once_with(
            ["cmd"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS,
        )

    def test_raises_when_the_client_outlives_the_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A client that never returns means no host answered it: raise, and adopt nothing."""
        client = _FakePopen(900, 0xB0, wait_error=self._timeout())
        self._patch_client(monkeypatch, client)
        app = WallpaperEngineApp(_EXE_PATH)

        with pytest.raises(subprocess.TimeoutExpired):
            app.apply_cli(["cmd"])

        assert app._popen is None


class TestOpenWallpaperCommand:
    """argv assembly for the ``openWallpaper`` control command."""

    def test_builds_the_minimal_command(self) -> None:
        app, apply_cli = _spy_app()

        app.open_wallpaper(file=r"C:\wallpapers\scene\project.json")

        apply_cli.assert_called_once_with(
            [
                str(_EXE_PATH),
                "-control",
                "openWallpaper",
                "-file",
                r"C:\wallpapers\scene\project.json",
            ]
        )

    def test_appends_only_the_supplied_optional_flags(self) -> None:
        app, apply_cli = _spy_app()

        app.open_wallpaper(
            file="w.json",
            location="monitor0",
            width=1920,
            height=1080,
            x=0,
            y=0,
            preset="day",
        )

        assert apply_cli.call_args.args[0] == [
            str(_EXE_PATH),
            "-control",
            "openWallpaper",
            "-file",
            "w.json",
            "-location",
            "monitor0",
            "-width",
            "1920",
            "-height",
            "1080",
            "-x",
            "0",
            "-y",
            "0",
            "-preset",
            "day",
        ]

    def test_string_monitor_is_quoted_but_an_int_monitor_is_not(self) -> None:
        app, apply_cli = _spy_app()

        app.open_wallpaper(file="w.json", monitor=_DEVICE_PATH)
        app.open_wallpaper(file="w.json", monitor=1)

        quoted, plain = (each.args[0][-2:] for each in apply_cli.call_args_list)
        assert quoted == ["-monitor", f'"{_DEVICE_PATH}"']
        assert plain == ["-monitor", "1"]


class TestWallpaperEngineCliCommands:
    """argv assembly for the remaining ``-control`` commands."""

    def test_play_and_stop_wallpaper_send_play_and_stop(self) -> None:
        app, apply_cli = _spy_app()

        app.play_wallpaper()
        app.stop_wallpaper()

        assert apply_cli.call_args_list == [
            call([str(_EXE_PATH), "-control", "play"]),
            call([str(_EXE_PATH), "-control", "stop"]),
        ]

    def test_close_wallpaper_without_targets_sends_close_wallpaper(self) -> None:
        app, apply_cli = _spy_app()

        app.close_wallpaper()

        apply_cli.assert_called_once_with([str(_EXE_PATH), "-control", "closeWallpaper"])

    def test_close_wallpaper_forwards_location_and_monitor(self) -> None:
        app, apply_cli = _spy_app()

        app.close_wallpaper(location="monitor0", monitor=_DEVICE_PATH)

        apply_cli.assert_called_once_with(
            [
                str(_EXE_PATH),
                "-control",
                "closeWallpaper",
                "-location",
                "monitor0",
                "-monitor",
                f'"{_DEVICE_PATH}"',
            ]
        )


class TestMonitorDetection:
    """Reading and rewriting ``monitordetection`` in Wallpaper Engine's config.json."""

    @staticmethod
    def _app_for(config_path: Path) -> WallpaperEngineApp:
        """Return an app pointed at ``config_path`` without starting anything."""
        app = WallpaperEngineApp(_EXE_PATH)
        app.config_path = config_path
        return app

    def test_enforce_writes_devicepath_and_captures_the_original(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        _write_user_config(config_path, {"monitordetection": "monitorindex"})
        app = self._app_for(config_path)

        assert app.enforce_monitor_detection_devicepath() is True
        assert app._original_monitor_detection == "monitorindex"
        assert _read_user_section(config_path)["monitordetection"] == "devicepath"

    def test_enforce_leaves_an_already_devicepath_config_untouched(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        _write_user_config(config_path, {"monitordetection": "devicepath"})
        app = self._app_for(config_path)
        before = config_path.read_text(encoding="utf-8")

        assert app.enforce_monitor_detection_devicepath() is False
        assert app._original_monitor_detection is None
        assert config_path.read_text(encoding="utf-8") == before

    def test_enforce_raises_when_monitordetection_is_missing(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        _write_user_config(config_path, {})
        app = self._app_for(config_path)

        with pytest.raises(KeyError, match="monitordetection"):
            app.enforce_monitor_detection_devicepath()

    def test_enforce_raises_on_invalid_json(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text("not json", encoding="utf-8")
        app = self._app_for(config_path)

        with pytest.raises(json.JSONDecodeError):
            app.enforce_monitor_detection_devicepath()

    def test_restore_writes_back_the_captured_value(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        _write_user_config(config_path, {"monitordetection": "devicepath"})
        app = self._app_for(config_path)
        app._original_monitor_detection = "monitorindex"

        app.restore_monitor_detection()

        assert _read_user_section(config_path)["monitordetection"] == "monitorindex"
        assert app._original_monitor_detection is None

    def test_restore_is_a_no_op_when_nothing_was_captured(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        _write_user_config(config_path, {"monitordetection": "devicepath"})
        app = self._app_for(config_path)
        before = config_path.read_text(encoding="utf-8")

        app.restore_monitor_detection()

        assert config_path.read_text(encoding="utf-8") == before


class TestWallpaperEngineResourceEnsureApp:
    """Creating the shared app once, with the teardown armed before it starts."""

    def test_creates_and_starts_the_shared_app_on_first_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        start = MagicMock()
        monkeypatch.setattr(WallpaperEngineApp, "start", start)

        app = WallpaperEngineResource._ensure_app()

        assert WallpaperEngineResource.app is app
        start.assert_called_once_with()

    def test_registers_the_app_stop_callback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(WallpaperEngineApp, "start", MagicMock())
        atexit = _stub_atexit(monkeypatch)

        app = WallpaperEngineResource._ensure_app()

        atexit.register.assert_called_once_with(app.stop)

    def test_registers_atexit_before_starting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        order: list[str] = []
        monkeypatch.setattr(
            WallpaperEngineApp,
            "start",
            MagicMock(side_effect=lambda: order.append("start")),
        )
        atexit = _stub_atexit(monkeypatch)
        atexit.register.side_effect = lambda callback: order.append("register")

        WallpaperEngineResource._ensure_app()

        assert order == ["register", "start"]

    def test_reuses_the_same_app_on_subsequent_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        start = MagicMock()
        monkeypatch.setattr(WallpaperEngineApp, "start", start)
        atexit = _stub_atexit(monkeypatch)

        first = WallpaperEngineResource._ensure_app()
        second = WallpaperEngineResource._ensure_app()

        assert first is second
        start.assert_called_once_with()
        atexit.register.assert_called_once()

    def test_an_existing_app_is_returned_without_restarting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        start = MagicMock()
        monkeypatch.setattr(WallpaperEngineApp, "start", start)
        atexit = _stub_atexit(monkeypatch)
        existing = WallpaperEngineApp()
        WallpaperEngineResource.app = existing

        assert WallpaperEngineResource._ensure_app() is existing
        start.assert_not_called()
        atexit.register.assert_not_called()


class TestWallpaperEngineResourceMount:
    """Opening the bound wallpaper and resuming playback."""

    @staticmethod
    def _resource(
        monkeypatch: pytest.MonkeyPatch, display: DisplayInfo | None
    ) -> tuple[WallpaperEngineResource, MagicMock]:
        """Return a resource bound to ``display``, with a spied shared app."""
        app = MagicMock()
        monkeypatch.setattr(WallpaperEngineResource, "_ensure_app", MagicMock(return_value=app))
        resource = WallpaperEngineResource(path=r"C:\wallpapers\scene")
        if display is not None:
            resource._bind_display(display)
        return resource, app

    def test_mount_opens_then_plays_on_the_bound_display(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resource, app = self._resource(monkeypatch, _make_display())

        resource.mount()

        assert app.method_calls == [
            call.open_wallpaper(file=resource.image_path, monitor=_DEVICE_PATH),
            call.play_wallpaper(),
        ]

    def test_mount_raises_when_no_display_is_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resource, _ = self._resource(monkeypatch, None)

        with pytest.raises(AssertionError):
            resource.mount()


class TestWallpaperEngineResourceDemount:
    """Closing the bound wallpaper, and rejecting the states ``mount()`` rules out."""

    def test_demount_raises_without_the_shared_app(self) -> None:
        """An uninitialized app is an internal error, even with a display bound."""
        resource = WallpaperEngineResource(path="scene")
        resource._bind_display(_make_display())

        with pytest.raises(RuntimeError, match="app not initialized"):
            resource.demount()

    def test_demount_raises_without_a_bound_display(self) -> None:
        """An unbound display leaves no monitor to close, so it is an internal error."""
        app = MagicMock()
        WallpaperEngineResource.app = app
        resource = WallpaperEngineResource(path="scene")

        with pytest.raises(RuntimeError, match="display not bound"):
            resource.demount()
        app.close_wallpaper.assert_not_called()

    def test_demount_closes_the_wallpaper_on_the_bound_display(self) -> None:
        app = MagicMock()
        WallpaperEngineResource.app = app
        resource = WallpaperEngineResource(path="scene")
        resource._bind_display(_make_display())

        resource.demount()

        app.close_wallpaper.assert_called_once_with(monitor=_DEVICE_PATH)
