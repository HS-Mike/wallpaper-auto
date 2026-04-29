import pytest
import requests
import datetime
from unittest.mock import patch

from wallpaper_automator.evaluator.workday_evaluator import is_workday, WorkdayEvaluator


@pytest.fixture(autouse=True)
def clear_api_cache():
    """Clear the @cache on is_workday before and after each test."""
    if hasattr(is_workday, 'cache_clear'):
        is_workday.cache_clear()
    yield
    if hasattr(is_workday, 'cache_clear'):
        is_workday.cache_clear()


class TestWorkdayLogic:

    @pytest.mark.parametrize("api_type, expected_bool", [
        (0, True),  (3, True), (1, False), (2, False),
    ])
    def test_is_workday_logic_branches(self, api_type, expected_bool):
        """API type 0/3 = workday, 1/2 = not workday."""
        mock_resp = {"code": 0, "type": {"type": api_type}}
        with patch('requests.get') as mock_get:
            mock_get.return_value.json.return_value = mock_resp
            mock_get.return_value.status_code = 200
            # Test string input
            assert is_workday("2024-01-01") == expected_bool
            is_workday.cache_clear()
            # Test date object input
            assert is_workday(datetime.date(2024, 1, 1)) == expected_bool

    def test_api_error_code(self):
        with patch('requests.get') as mock_get:
            mock_get.return_value.json.return_value = {"code": -1, "msg": "invalid"}
            mock_get.return_value.status_code = 200
            assert is_workday("2024-01-01") is None

    @pytest.mark.parametrize("exception", [
        requests.exceptions.ConnectTimeout,
        requests.exceptions.HTTPError,
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        Exception,
    ])
    def test_network_exceptions(self, exception):
        with patch('requests.get', side_effect=exception):
            assert is_workday("2024-01-01") is None

    def test_invalid_param_type_raises(self):
        """Non-bool param raises ValueError."""
        evaluator = WorkdayEvaluator()
        with pytest.raises(ValueError, match="invalid WorkdayEvaluator param"):
            evaluator("not_a_bool")
        with pytest.raises(ValueError, match="invalid WorkdayEvaluator param"):
            evaluator(123)

    def test_evaluator_call_logic(self):
        """Test WorkdayEvaluator.__call__ with patched is_workday."""
        evaluator = WorkdayEvaluator()
        target_path = 'wallpaper_automator.evaluator.workday_evaluator.is_workday'

        with patch(target_path) as mock_func:
            # API down
            mock_func.return_value = None
            assert not evaluator(True)
            assert not evaluator(False)

            # Match success
            mock_func.return_value = True
            assert evaluator(True)
            assert not evaluator(False)

            # Match failure
            mock_func.return_value = False
            assert not evaluator(True)
            assert evaluator(False)


class TestRealAPI:
    """Real connectivity tests (may be skipped in CI)."""

    def test_actual_timor_tech_api(self):
        res = is_workday("2024-01-01")
        assert not res

    def test_today_api_status(self):
        res = is_workday(datetime.datetime.now())
        assert isinstance(res, bool)
