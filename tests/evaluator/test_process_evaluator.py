"""Tests for process_evaluator.py — process-running condition check."""

from unittest.mock import patch

import pytest

from wallpaper_auto.evaluator.process_evaluator import ProcessEvaluator

_MOD = "wallpaper_auto.evaluator.process_evaluator"


@pytest.fixture
def evaluator():
    return ProcessEvaluator()


class TestProcessEvaluator:
    def test_basename_match(self, evaluator):
        with patch(f"{_MOD}.get_running_processes", return_value=[(100, "notepad.exe")]):
            assert evaluator("notepad.exe")

    def test_basename_match_case_insensitive(self, evaluator):
        with patch(f"{_MOD}.get_running_processes", return_value=[(100, "NOTEPAD.EXE")]):
            assert evaluator("notepad.exe")

    def test_basename_matches_any_running_process(self, evaluator):
        with patch(
            f"{_MOD}.get_running_processes",
            return_value=[(100, "explorer.exe"), (200, "chrome.exe")],
        ):
            assert evaluator("chrome.exe")

    def test_returns_false_when_basename_not_running(self, evaluator):
        with patch(f"{_MOD}.get_running_processes", return_value=[(100, "explorer.exe")]):
            assert not evaluator("notepad.exe")

    def test_full_path_match(self, evaluator):
        with (
            patch(f"{_MOD}.get_running_processes", return_value=[(100, "notepad.exe")]),
            patch(
                f"{_MOD}.get_executable_path",
                return_value=r"C:\Windows\System32\notepad.exe",
            ),
        ):
            assert evaluator(r"C:\Windows\System32\notepad.exe")

    def test_full_path_matches_any_running_instance(self, evaluator):
        with (
            patch(
                f"{_MOD}.get_running_processes",
                return_value=[(100, "notepad.exe"), (200, "notepad.exe")],
            ),
            patch(
                f"{_MOD}.get_executable_path",
                side_effect=[r"C:\Other\notepad.exe", r"C:\Windows\System32\notepad.exe"],
            ),
        ):
            assert evaluator(r"C:\Windows\System32\notepad.exe")

    def test_full_path_no_match_on_different_path(self, evaluator):
        with (
            patch(f"{_MOD}.get_running_processes", return_value=[(100, "notepad.exe")]),
            patch(f"{_MOD}.get_executable_path", return_value=r"C:\Other\notepad.exe"),
        ):
            assert not evaluator(r"C:\Windows\System32\notepad.exe")

    def test_full_path_no_match_when_path_unresolved(self, evaluator):
        with (
            patch(f"{_MOD}.get_running_processes", return_value=[(100, "notepad.exe")]),
            patch(f"{_MOD}.get_executable_path", return_value=None),
        ):
            assert not evaluator(r"C:\Windows\System32\notepad.exe")

    def test_returns_false_when_query_fails(self, evaluator):
        with patch(f"{_MOD}.get_running_processes", return_value=None):
            assert not evaluator("notepad.exe")

    def test_returns_false_when_no_processes(self, evaluator):
        with patch(f"{_MOD}.get_running_processes", return_value=[]):
            assert not evaluator("notepad.exe")

    def test_returns_false_when_param_blank(self, evaluator):
        with patch(f"{_MOD}.get_running_processes") as mock_enum:
            assert not evaluator("")
        mock_enum.assert_not_called()

    def test_returns_false_when_param_whitespace(self, evaluator):
        with patch(f"{_MOD}.get_running_processes") as mock_enum:
            assert not evaluator("   ")
        mock_enum.assert_not_called()

    def test_raises_when_param_not_string(self, evaluator):
        with pytest.raises(ValueError, match="invalid ProcessEvaluator param"):
            evaluator(123)  # type: ignore[arg-type]

    def test_raises_when_param_is_none(self, evaluator):
        with pytest.raises(ValueError, match="invalid ProcessEvaluator param"):
            evaluator(None)  # type: ignore[arg-type]
