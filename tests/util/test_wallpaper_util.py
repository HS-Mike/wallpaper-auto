"""Tests for util/wallpaper_util.py — Windows COM IDesktopWallpaper wrapper."""

import contextvars
import ctypes
from ctypes import wintypes
from unittest.mock import MagicMock, patch

import pytest

import wallpaper_auto.util.wallpaper_util as wu


@pytest.fixture(autouse=True)
def _fresh_context_var(monkeypatch):
    """Give each test its own ContextVar so ``.get()`` raises LookupError
    when nothing has been set. (``set(None)`` would not raise — the
    ContextVar would just yield ``None`` instead of being considered
    "unset".)"""
    monkeypatch.setattr(
        wu, "_current_p_wallpaper", contextvars.ContextVar("_test_p_wallpaper")
    )
    yield


def _mock_ole32(monkeypatch, *, co_init_return: int = 0,
                cocreate_return: int = 0):
    """Replace ``wu.ole32`` with a MagicMock that does NOT touch the
    byref passed to ``CoCreateInstance``. The mock therefore leaves
    ``p_wallpaper.value`` at 0 and the Release branch is skipped.
    Suitable for tests that don't need to exercise ``Release``.
    """
    fake = MagicMock()
    fake.CoInitialize.return_value = co_init_return
    fake.CoCreateInstance.return_value = cocreate_return
    monkeypatch.setattr(wu, "ole32", fake)
    return fake


def _write_to_byref(byref_arg, ctype, value):
    ptr = ctypes.cast(byref_arg, ctypes.POINTER(ctype))
    ptr[0] = value


class TestConstants:
    def test_dwpos_values(self):
        assert wu.DWPOS_CENTER == 0
        assert wu.DWPOS_TILE == 1
        assert wu.DWPOS_STRETCH == 2
        assert wu.DWPOS_FIT == 3
        assert wu.DWPOS_FILL == 4
        assert wu.DWPOS_SPAN == 5

    def test_slideshow_directions(self):
        assert wu.DSD_FORWARD == 0
        assert wu.DSD_BACKWARD == 1

    def test_status_codes(self):
        assert wu.SDB_READY == 0x00
        assert wu.SDB_RUNNING == 0x01
        assert wu.SDB_PAUSED == 0x02


class TestWallpaperStyle:
    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (wu.WallpaperStyle.CENTER, wu.DWPOS_CENTER),
            (wu.WallpaperStyle.TILE, wu.DWPOS_TILE),
            (wu.WallpaperStyle.STRETCH, wu.DWPOS_STRETCH),
            (wu.WallpaperStyle.FIT, wu.DWPOS_FIT),
            (wu.WallpaperStyle.FILL, wu.DWPOS_FILL),
            (wu.WallpaperStyle.SPAN, wu.DWPOS_SPAN),
        ],
    )
    def test_enum_matches_dwpos(self, member, expected):
        assert int(member) == expected


class TestMakeGuid:
    def test_returns_16_byte_buffer(self):
        g = wu.make_guid("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")
        assert isinstance(g, ctypes.Array)
        assert len(g) == 16

    def test_known_guid_bytes(self):
        # c_byte is signed; mask to compare against unsigned expected bytes.
        g = wu.make_guid("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")
        # First group C2CF3110 -> 10 31 CF C2 (little-endian DWORD).
        assert [b & 0xFF for b in g[:4]] == [0x10, 0x31, 0xCF, 0xC2]
        # Second group 460E -> 0E 46 (little-endian WORD).
        assert [b & 0xFF for b in g[4:6]] == [0x0E, 0x46]
        # Third group 4fc1 -> C1 4F.
        assert [b & 0xFF for b in g[6:8]] == [0xC1, 0x4F]


class TestCallComVtable:
    def test_vtable_call_dispatches_to_func(self, _fresh_context_var):
        called_with = []

        @ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_int)
        def fake_func(p_object, arg1, arg2):
            called_with.append((p_object, arg1, arg2))
            return 42

        vtable = (ctypes.c_void_p * 16)()
        vtable[7] = ctypes.cast(fake_func, ctypes.c_void_p).value

        # Production code does:
        #   p_object = .get()                       # c_void_p wrapping the int
        #   ppv = cast(p_object, POINTER(c_void_p)) # same address
        #   vtable = cast(ppv.contents, POINTER(...))# ppv.contents is the
        #                                            # first c_void_p at the
        #                                            # address -> vtable ptr
        # Therefore ``get()`` must return the address of a one-cell c_void_p
        # whose contents is ``addressof(vtable_array)``.
        buffer = (ctypes.c_void_p * 1)(ctypes.addressof(vtable))
        wu._current_p_wallpaper.set(ctypes.c_void_p(ctypes.addressof(buffer)))

        result = wu._call_com_vtable(
            7,
            ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_int),
            1,
            2,
        )

        assert result == 42
        # The COM pointer passed to the vtable func is the raw address stored
        # in the ContextVar (``addressof(buffer)``), not the vtable itself.
        assert called_with == [(ctypes.addressof(buffer), 1, 2)]


class TestComManaged:
    def test_uses_existing_com_context(self, _fresh_context_var, monkeypatch):
        """When the ContextVar is set, the inner func runs without COM init."""
        @wu.com_managed
        def inner(x):
            return x * 2

        wu._current_p_wallpaper.set(ctypes.c_void_p(0xDEAD))

        fake = MagicMock()
        monkeypatch.setattr(wu, "ole32", fake)

        assert inner(5) == 10
        fake.CoInitialize.assert_not_called()
        fake.CoUninitialize.assert_not_called()

    def test_lookup_error_from_inner_propagates(self, _fresh_context_var,
                                                monkeypatch):
        """LookupError raised inside the wrapped func must not be swallowed."""
        @wu.com_managed
        def inner():
            raise LookupError("from inner")

        wu._current_p_wallpaper.set(ctypes.c_void_p(0xDEAD))

        with patch.object(wu, "ole32"):
            with pytest.raises(LookupError, match="from inner"):
                inner()

    def test_initializes_and_tears_down(self, _fresh_context_var):
        """With no surrounding COM context, ``com_managed`` runs a real
        CoInitialize / CoCreateInstance / Release / CoUninitialize cycle
        around a public read-only call. The Release is the real
        ``IUnknown::Release`` on the actual ``IDesktopWallpaper`` instance.
        """
        count = wu.get_monitor_device_path_count()
        assert isinstance(count, int)
        assert count >= 0

    def test_co_initialize_returns_s_false(self, _fresh_context_var,
                                           monkeypatch):
        """S_FALSE (1) from CoInitialize is treated as a successful init."""
        @wu.com_managed
        def inner(x):
            return x

        fake = _mock_ole32(monkeypatch, co_init_return=1)

        assert inner(1) == 1
        fake.CoInitialize.assert_called_once_with(None)
        # S_FALSE still means *we* initialised COM -> CoUninitialize runs.
        fake.CoUninitialize.assert_called_once()

    def test_co_initialize_returns_other_value_skips_uninit(
        self, _fresh_context_var, monkeypatch
    ):
        """A CoInitialize return code other than S_OK / S_FALSE (e.g. E_FAIL)
        means the caller already owns COM lifetime -> no CoUninitialize."""
        @wu.com_managed
        def inner(x):
            return x

        fake = _mock_ole32(monkeypatch, co_init_return=2)

        assert inner(0) == 0
        fake.CoInitialize.assert_called_once()
        fake.CoUninitialize.assert_not_called()

    def test_cocreate_failure_raises(self, _fresh_context_var, monkeypatch):
        @wu.com_managed
        def inner(x):
            return x

        fake = MagicMock()
        fake.CoInitialize.return_value = 0
        fake.CoCreateInstance.return_value = -1
        monkeypatch.setattr(wu, "ole32", fake)

        with pytest.raises(OSError, match="CoCreateInstance failed"):
            inner(1)


class TestComSession:
    def test_session_yields_and_releases(self, _fresh_context_var):
        """``com_session`` does a real ``CoInitialize`` / ``CoCreateInstance``
        on entry and a real ``IUnknown::Release`` / ``CoUninitialize`` on
        exit. The test fails if either cleanup step would error (e.g.
        wrong calling convention on the real Release trampoline).
        """
        with wu.com_session():
            # Inside the block the ContextVar holds a real COM pointer
            # obtained from the genuine IDesktopWallpaper factory.
            assert wu._current_p_wallpaper.get() is not None
            # Read-only call — no real-world side effects on the wallpaper.
            count = wu.get_monitor_device_path_count()
            assert isinstance(count, int)
            assert count >= 0
        # Exit: the with block calls Release on the real COM pointer and
        # then CoUninitialize. If we got here, both completed cleanly.

    def test_session_cocreate_failure_raises(self, _fresh_context_var,
                                             monkeypatch):
        fake = MagicMock()
        fake.CoInitialize.return_value = 0
        fake.CoCreateInstance.return_value = -1
        monkeypatch.setattr(wu, "ole32", fake)

        with pytest.raises(OSError, match="CoCreateInstance failed"):
            with wu.com_session():
                pass

        # p_wallpaper.value stayed 0 — no release path — but COM was
        # initialised so CoUninitialize must still run.
        fake.CoUninitialize.assert_called_once()


class TestGetMonitorDevicePathCount:
    def test_returns_count(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))

        def _vtable(*args):
            # args = (index, prototype, byref(count))
            _write_to_byref(args[2], ctypes.c_uint, ctypes.c_uint(3))
            return 0

        with patch.object(wu, "_call_com_vtable", side_effect=_vtable):
            assert wu.get_monitor_device_path_count() == 3

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="failed to get monitor count"):
                wu.get_monitor_device_path_count()


class TestGetMonitorDevicePathAt:
    def test_returns_path(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))

        _keepalive: list[object] = []

        def _vtable(*args):
            # args = (5, PROTO_GET_MONITOR_PATH_AT, UINT, byref(monitor_id))
            value = ctypes.c_wchar_p("MONITOR\\ABC")
            _keepalive.append(value)
            ptr = ctypes.cast(args[3], ctypes.POINTER(ctypes.c_wchar_p))
            ptr[0] = value
            return 0

        with (
            patch.object(wu, "_call_com_vtable", side_effect=_vtable),
            patch.object(wu.ole32, "CoTaskMemFree"),
        ):
            assert wu.get_monitor_device_path_at(0) == "MONITOR\\ABC"

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="failed to get monitor path"):
                wu.get_monitor_device_path_at(0)

    def test_null_path_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))

        def _vtable(*args):
            ptr = ctypes.cast(args[3], ctypes.POINTER(ctypes.c_wchar_p))
            ptr[0] = None
            return 0

        with patch.object(wu, "_call_com_vtable", side_effect=_vtable):
            with pytest.raises(OSError, match="monitor path is empty"):
                wu.get_monitor_device_path_at(0)


class TestSetWallpaper:
    def test_sets_wallpaper(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.set_wallpaper("MONITOR\\1", "C:/bg.jpg")
        # args = (3, PROTO_SET_WALLPAPER, monitor_id, str(image_path))
        assert mock_call.call_args.args[2] == "MONITOR\\1"
        assert mock_call.call_args.args[3] == "C:/bg.jpg"

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="set_wallpaper failed"):
                wu.set_wallpaper(None, "C:/bg.jpg")

    def test_accepts_path_object(self, _fresh_context_var):
        from pathlib import Path

        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.set_wallpaper(None, Path("C:/bg.jpg"))
        # The Path is str()'d before being passed to the COM call.
        assert str(mock_call.call_args.args[3]).replace("\\", "/") == "C:/bg.jpg"


class TestGetWallpaper:
    def test_returns_path(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))

        _keepalive: list[object] = []

        def _vtable(*args):
            # args = (4, PROTO_GET_WALLPAPER, monitor_id, byref(path))
            value = ctypes.c_wchar_p("C:\\wp.jpg")
            _keepalive.append(value)
            ptr = ctypes.cast(args[3], ctypes.POINTER(ctypes.c_wchar_p))
            ptr[0] = value
            return 0

        with (
            patch.object(wu, "_call_com_vtable", side_effect=_vtable),
            patch.object(wu.ole32, "CoTaskMemFree"),
        ):
            result = wu.get_wallpaper(None)
        # On Windows, ctypes normalises forward slashes to backslashes in paths.
        assert str(result).replace("\\", "/") == "C:/wp.jpg"

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="get_wallpaper failed"):
                wu.get_wallpaper(None)

    def test_null_path_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))

        def _vtable(*args):
            ptr = ctypes.cast(args[3], ctypes.POINTER(ctypes.c_wchar_p))
            ptr[0] = None
            return 0

        with patch.object(wu, "_call_com_vtable", side_effect=_vtable):
            with pytest.raises(OSError, match="wallpaper path returned Null"):
                wu.get_wallpaper(None)


class TestGetMonitorBounds:
    def test_returns_bounds(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))

        def _vtable(*args):
            # args = (7, PROTO_GET_MONITOR_BOUNDS, monitor_id, byref(RECT))
            ptr = ctypes.cast(args[3], ctypes.POINTER(wu.RECT))
            ptr[0].left = 0
            ptr[0].top = 0
            ptr[0].right = 1920
            ptr[0].bottom = 1080
            return 0

        with patch.object(wu, "_call_com_vtable", side_effect=_vtable):
            result = wu.get_monitor_bounds("MON")
        assert result.left == 0
        assert result.top == 0
        assert result.right == 1920
        assert result.bottom == 1080

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="get_monitor_bounds failed"):
                wu.get_monitor_bounds("MON")


class TestSetBackgroundColor:
    def test_int_color(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.set_background_color(0x00FF0080)
        # args = (8, PROTO_SET_BACKGROUND_COLOR, COLORREF)
        arg = mock_call.call_args.args[2]
        assert int(arg.value) == 0x00FF0080

    def test_rgb_tuple(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.set_background_color((10, 20, 30))
        expected = 10 | (20 << 8) | (30 << 16)
        arg = mock_call.call_args.args[2]
        assert int(arg.value) == expected

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="set_background_color failed"):
                wu.set_background_color(0)


class TestGetBackgroundColor:
    def test_returns_rgb(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        def _vtable(*args):
            # args = (9, PROTO_GET_BACKGROUND_COLOR, byref(COLORREF))
            ptr = ctypes.cast(args[2], ctypes.POINTER(wintypes.COLORREF))
            ptr[0] = wintypes.COLORREF(0x00102030)
            return 0

        with patch.object(wu, "_call_com_vtable", side_effect=_vtable):
            r, g, b = wu.get_background_color()
        # val = 0x30 | (0x20 << 8) | (0x10 << 16) -> r=0x30, g=0x20, b=0x10.
        assert (r, g, b) == (0x30, 0x20, 0x10)

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="get_background_color failed"):
                wu.get_background_color()


class TestSetWallpaperStyle:
    def test_sets_style(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.set_wallpaper_style(wu.WallpaperStyle.FILL)
        # args = (10, PROTO_SET_WALLPAPER_POS, int(style))
        assert mock_call.call_args.args[2] == 4  # DWPOS_FILL

    def test_accepts_int(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.set_wallpaper_style(wu.DWPOS_SPAN)
        assert mock_call.call_args.args[2] == 5  # DWPOS_SPAN

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="set_wallpaper_style failed"):
                wu.set_wallpaper_style(0)


class TestGetWallpaperStyle:
    def test_returns_style(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))

        def _vtable(*args):
            # args = (11, PROTO_GET_WALLPAPER_POS, byref(style))
            ptr = ctypes.cast(args[2], ctypes.POINTER(ctypes.c_int))
            ptr[0] = 4  # FILL
            return 0

        with patch.object(wu, "_call_com_vtable", side_effect=_vtable):
            assert wu.get_wallpaper_style() is wu.WallpaperStyle.FILL

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="get_wallpaper_style failed"):
                wu.get_wallpaper_style()


class TestAdvanceSlideshow:
    def test_default_is_forward(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.advance_slideshow(None)
        # args = (14, PROTO_ADVANCE_SLIDESHOW, monitor_id, direction)
        arg = mock_call.call_args.args[3]
        assert int(arg.value) == 0  # DSD_FORWARD

    def test_backward(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.advance_slideshow(None, forward=False)
        arg = mock_call.call_args.args[3]
        assert int(arg.value) == 1  # DSD_BACKWARD

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="advance_slideshow failed"):
                wu.advance_slideshow(None)


class TestGetStatus:
    def test_returns_status(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))

        def _vtable(*args):
            # args = (15, PROTO_GET_STATUS, byref(status))
            ptr = ctypes.cast(args[2], ctypes.POINTER(ctypes.c_uint32))
            ptr[0] = ctypes.c_uint32(2)  # SDB_PAUSED
            return 0

        with patch.object(wu, "_call_com_vtable", side_effect=_vtable):
            assert wu.get_status() == 2

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="get_status failed"):
                wu.get_status()


class TestEnable:
    def test_enable_true(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.enable(True)
        # args = (16, PROTO_ENABLE, BOOL(enable_))
        arg = mock_call.call_args.args[2]
        assert bool(arg) is True

    def test_enable_false(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=0) as mock_call:
            wu.enable(False)
        arg = mock_call.call_args.args[2]
        assert bool(arg) is False

    def test_negative_hr_raises(self, _fresh_context_var):
        wu._current_p_wallpaper.set(ctypes.c_void_p(0))
        with patch.object(wu, "_call_com_vtable", return_value=-1):
            with pytest.raises(OSError, match="enable failed"):
                wu.enable(True)
