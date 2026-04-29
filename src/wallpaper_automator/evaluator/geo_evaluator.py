"""
Geolocation condition evaluator.

Resolves the current public IP via ip-api.com and uses the Haversine formula
to compute whether the machine is within a given radius (in km) of a target coordinate.
"""
import math
from functools import cache
from typing import TypedDict, cast

import requests

from .base_evaluator import BaseEvaluator


class LocationInfo(TypedDict):
    ip: str
    lat: float
    lon: float
    city: str
    regionName: str
    country: str


@cache
def get_location_info_by_ip(timeout: float = 3) -> LocationInfo | None:
    try:
        response = requests.get('http://ip-api.com/json/', timeout=timeout)
    except requests.exceptions.RequestException:
        return None
    else:
        data = response.json()
        if data['status'] == 'success':
            res: LocationInfo = {
                'ip': data['query'],
                'lat': data['lat'],
                'lon': data['lon'],
                'city': data['city'],
                'regionName': data['regionName'],
                'country': data['country']
            }
            return res
        else:
            return None


R = 6371  # Earth's mean radius in kilometers


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the distance between two lat/lon coordinates (Haversine formula).
    Returns distance in kilometers.
    """
    # Convert to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    distance = R * c
    return distance


class GeoEvaluator(BaseEvaluator):

    def _validate_params(self, param: dict) -> tuple[float, float, float]:
        lat = param.get("lat")
        lon = param.get("lon")
        radius = param.get("radius")

        errors = []
        if not isinstance(lat, (int, float)):
            errors.append("lat must be a number")
        elif not -90 <= lat <= 90:
            errors.append("lat must be between -90 and 90")

        if not isinstance(lon, (int, float)):
            errors.append("lon must be a number")
        elif not -180 <= lon <= 180:
            errors.append("lon must be between -180 and 180")

        if not isinstance(radius, (int, float)):
            errors.append("radius must be a number")
        elif radius <= 0:
            errors.append("radius must be positive")

        if errors:
            raise ValueError(f"Invalid GeoEvaluator params: {'; '.join(errors)}")

        return cast(float, lat), cast(float, lon), cast(float, radius)

    def __call__(self, param: dict):
        lat, lon, radius = self._validate_params(param)

        loc_info = get_location_info_by_ip()
        if loc_info is None:
            return False
        dist = haversine_distance(
            lat, lon, loc_info["lat"], loc_info["lon"]
        )
        return dist <= radius
