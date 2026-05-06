"""Tests for geo_evaluator.py — IP geolocation and distance calculation."""

import pytest
from unittest.mock import MagicMock, patch
import math

from wallpaper_automator.evaluator.geo_evaluator import (
    get_location_info_by_ip,
    haversine_distance,
    LocationInfo,
    R,
    GeoEvaluator,
)


@pytest.fixture
def clear_cache():
    """Clear the @cache on get_location_info_by_ip between tests."""
    get_location_info_by_ip.cache_clear()


@pytest.fixture
def mock_ip_api():
    with patch("wallpaper_automator.evaluator.geo_evaluator.requests.get") as mock_get:
        yield mock_get


SAMPLE_RESPONSE = {
    "status": "success",
    "query": "8.8.8.8",
    "lat": 37.3861,
    "lon": -122.0839,
    "city": "Mountain View",
    "regionName": "California",
    "country": "United States",
}


@pytest.mark.usefixtures("clear_cache")
class TestGetLocationByIp:
    def setup_mock(self, mock_get, data, exc=None):
        mock_response = MagicMock()
        mock_response.json.return_value = data
        mock_get.return_value = mock_response
        if exc:
            mock_get.side_effect = exc
        return mock_response

    def test_successful_response(self, mock_ip_api):
        self.setup_mock(mock_ip_api, SAMPLE_RESPONSE)
        result = get_location_info_by_ip(timeout=3)

        assert result == {
            "ip": "8.8.8.8",
            "lat": 37.3861,
            "lon": -122.0839,
            "city": "Mountain View",
            "regionName": "California",
            "country": "United States",
        }
        mock_ip_api.assert_called_once_with("http://ip-api.com/json/", timeout=3)

    def test_api_returns_failure(self, mock_ip_api):
        self.setup_mock(mock_ip_api, {"status": "fail", "message": "invalid query"})
        assert get_location_info_by_ip() is None

    def test_timeout_error(self, mock_ip_api):
        mock_ip_api.side_effect = TimeoutError("Connection timeout")
        with pytest.raises(TimeoutError):
            get_location_info_by_ip(timeout=1)

    def test_network_error_propagates(self, mock_ip_api):
        mock_ip_api.side_effect = Exception("Network error")
        with pytest.raises(Exception):
            get_location_info_by_ip()

    def test_timeout_is_requestexception(self, mock_ip_api):
        """requests.exceptions.RequestException subclasses return None."""
        import requests
        mock_ip_api.side_effect = requests.exceptions.ConnectTimeout("timed out")
        assert get_location_info_by_ip() is None

    def test_missing_fields_in_response(self, mock_ip_api):
        self.setup_mock(
            mock_ip_api,
            {"status": "success", "ip": "8.8.8.8", "lat": 37.3861, "city": "Mountain View"},
        )
        with pytest.raises(KeyError):
            get_location_info_by_ip()

    @pytest.mark.parametrize("timeout", [1, 5, 10])
    def test_custom_timeout(self, mock_ip_api, timeout):
        self.setup_mock(mock_ip_api, SAMPLE_RESPONSE)
        get_location_info_by_ip(timeout=timeout)
        mock_ip_api.assert_called_once_with("http://ip-api.com/json/", timeout=timeout)


class TestHaversineDistance:
    def test_same_point(self):
        lat, lon = 40.7128, -74.0060
        assert haversine_distance(lat, lon, lat, lon) == 0.0

    def test_known_distance_newyork_losangeles(self):
        distance = haversine_distance(40.7128, -74.0060, 34.0522, -118.2437)
        assert math.isclose(distance, 3936, rel_tol=0.05)

    def test_known_distance_london_paris(self):
        distance = haversine_distance(51.5074, -0.1278, 48.8566, 2.3522)
        assert math.isclose(distance, 344, rel_tol=0.05)

    def test_antipodal_points(self):
        distance = haversine_distance(0.0, 0.0, 0.0, 180.0)
        assert math.isclose(distance, math.pi * R, rel_tol=0.01)

    def test_equator_points(self):
        distance = haversine_distance(0, 0, 0, 1)
        assert math.isclose(distance, 111.32, rel_tol=0.01)

    def test_north_pole_to_equator(self):
        distance = haversine_distance(90.0, 0.0, 0.0, 0.0)
        assert math.isclose(distance, (math.pi * R) / 2, rel_tol=0.01)

    def test_commutative_property(self):
        a, b = (30.0, 120.0), (-20.0, 80.0)
        dist_ab = haversine_distance(*a, *b)
        dist_ba = haversine_distance(*b, *a)
        assert math.isclose(dist_ab, dist_ba, rel_tol=1e-10)

    @pytest.mark.parametrize(
        "lat1,lon1,lat2,lon2,expected_range",
        [
            (0, 0, 0, 0, (0, 0)),
            (90, 0, -90, 0, (20000, 20020)),
            (0, 0, 0, 180, (20000, 20020)),
        ],
    )
    def test_distance_ranges(self, lat1, lon1, lat2, lon2, expected_range):
        distance = haversine_distance(lat1, lon1, lat2, lon2)
        assert expected_range[0] <= distance <= expected_range[1]


@pytest.mark.usefixtures("clear_cache")
class TestGeoEvaluator:

    def test_returns_bool(self, mock_ip_api):
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_RESPONSE
        mock_ip_api.return_value = mock_response

        result = GeoEvaluator()({"lat": 0, "lon": 0, "radius": 10})
        assert isinstance(result, bool)

    def test_returns_false_on_api_error(self, mock_ip_api):
        import requests
        mock_ip_api.side_effect = requests.exceptions.ConnectTimeout("API unavailable")

        result = GeoEvaluator()({"lat": 0, "lon": 0, "radius": 10})
        assert not result


@pytest.mark.usefixtures("clear_cache")
class TestIntegrationGeo:

    def test_distance_from_shanghai(self, mock_ip_api):
        beijing = {
            "status": "success",
            "query": "123.123.123.123",
            "lat": 39.9042,
            "lon": 116.4074,
            "city": "Beijing",
            "regionName": "Beijing",
            "country": "China",
        }
        mock_response = MagicMock()
        mock_response.json.return_value = beijing
        mock_ip_api.return_value = mock_response

        loc_info = get_location_info_by_ip()
        assert loc_info is not None
        distance = haversine_distance(loc_info["lat"], loc_info["lon"], 31.23, 121.47)
        assert math.isclose(distance, 1067, rel_tol=0.05)


class TestGeoEvaluatorValidateParams:
    """Tests for _validate_params error branches (lines 77-92)."""

    def test_invalid_lat_type(self):
        with pytest.raises(ValueError, match="lat must be a number"):
            GeoEvaluator()({"lat": "invalid", "lon": 0, "radius": 10})

    def test_lat_out_of_range(self):
        with pytest.raises(ValueError, match="lat must be between -90 and 90"):
            GeoEvaluator()({"lat": 100, "lon": 0, "radius": 10})

    def test_invalid_lon_type(self):
        with pytest.raises(ValueError, match="lon must be a number"):
            GeoEvaluator()({"lat": 0, "lon": "invalid", "radius": 10})

    def test_lon_out_of_range(self):
        with pytest.raises(ValueError, match="lon must be between -180 and 180"):
            GeoEvaluator()({"lat": 0, "lon": 200, "radius": 10})

    def test_invalid_radius_type(self):
        with pytest.raises(ValueError, match="radius must be a number"):
            GeoEvaluator()({"lat": 0, "lon": 0, "radius": "invalid"})

    def test_radius_not_positive(self):
        with pytest.raises(ValueError, match="radius must be positive"):
            GeoEvaluator()({"lat": 0, "lon": 0, "radius": -1})

    def test_multiple_errors_combined(self):
        with pytest.raises(ValueError, match="lat must be a number.*lon must be a number"):
            GeoEvaluator()({"lat": "bad", "lon": "bad", "radius": "bad"})
