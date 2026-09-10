"""Mapbox imagery, geocoding and Web-Mercator conversion helpers."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from settings import settings


# Mapbox/Web-Mercator specification constants, not tuning parameters.
EARTH_CIRCUMFERENCE_M = 40_075_016.68557849
MAPBOX_STATIC_TILE_SIZE_PX = 512
WEB_MERCATOR_MAX_LATITUDE_DEG = 85.05112878
MINIMUM_MAPBOX_ZOOM = 1.0
MAXIMUM_STATIC_IMAGE_ZOOM = 18.0


def mapbox_token_is_configured() -> bool:
    token = settings.mapbox.token.strip()
    return bool(token and not token.startswith("pk.your_"))


def require_mapbox_token() -> str:
    if not mapbox_token_is_configured():
        raise RuntimeError("MAPBOX_TOKEN is missing from .env")
    return settings.mapbox.token.strip()


def metres_per_pixel(latitude_deg: float, zoom: float) -> float:
    return (
        EARTH_CIRCUMFERENCE_M
        * math.cos(math.radians(latitude_deg))
        / (MAPBOX_STATIC_TILE_SIZE_PX * 2**zoom)
    )


def zoom_for_metres_per_pixel(latitude_deg: float, target_metres_per_pixel: float) -> float:
    return math.log2(
        EARTH_CIRCUMFERENCE_M
        * math.cos(math.radians(latitude_deg))
        / (MAPBOX_STATIC_TILE_SIZE_PX * target_metres_per_pixel)
    )


def zoom_for_radius(
    latitude_deg: float,
    radius_m: float,
    image_width_px: int,
    image_height_px: int,
) -> float:
    image_diameter_m = radius_m * 2.0
    limiting_image_pixels = min(image_width_px, image_height_px)
    target_metres_per_pixel = image_diameter_m / limiting_image_pixels
    calculated_zoom = zoom_for_metres_per_pixel(latitude_deg, target_metres_per_pixel)
    return max(MINIMUM_MAPBOX_ZOOM, min(MAXIMUM_STATIC_IMAGE_ZOOM, calculated_zoom))


def fetch_satellite_image(
    latitude_deg: float,
    longitude_deg: float,
    zoom: float,
    width_px: int,
    height_px: int,
    destination: Path,
) -> None:
    token = require_mapbox_token()
    map_centre = f"{longitude_deg:.8f},{latitude_deg:.8f},{zoom:.3f},0"
    style = settings.mapbox.satellite_style.strip("/")
    request_url = (
        f"https://api.mapbox.com/styles/v1/{style}/static/"
        f"{map_centre}/{width_px}x{height_px}?access_token={quote(token, safe='')}"
    )

    response = requests.get(request_url, timeout=settings.mapbox.request_timeout_s)
    response.raise_for_status()

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)


def forward_geocode(query: str) -> dict[str, Any] | None:
    token = require_mapbox_token()
    request_url = "https://api.mapbox.com/search/geocode/v6/forward"
    request_parameters = {
        "q": query,
        "country": settings.mapbox.geocode_country_code,
        "limit": 1,
        "access_token": token,
    }

    response = requests.get(
        request_url,
        params=request_parameters,
        timeout=settings.mapbox.geocode_timeout_s,
    )
    response.raise_for_status()
    features = response.json().get("features", [])
    return features[0] if features else None


def lonlat_to_world_pixel(
    longitude_deg: float,
    latitude_deg: float,
    zoom: float,
) -> tuple[float, float]:
    world_size_px = MAPBOX_STATIC_TILE_SIZE_PX * 2**zoom
    world_x_px = (longitude_deg + 180.0) / 360.0 * world_size_px

    clamped_latitude = max(
        -WEB_MERCATOR_MAX_LATITUDE_DEG,
        min(WEB_MERCATOR_MAX_LATITUDE_DEG, latitude_deg),
    )
    sine_latitude = math.sin(math.radians(clamped_latitude))
    world_y_px = (
        0.5
        - math.log((1.0 + sine_latitude) / (1.0 - sine_latitude))
        / (4.0 * math.pi)
    ) * world_size_px
    return world_x_px, world_y_px


def world_pixel_to_lonlat(
    world_x_px: float,
    world_y_px: float,
    zoom: float,
) -> tuple[float, float]:
    world_size_px = MAPBOX_STATIC_TILE_SIZE_PX * 2**zoom
    longitude_deg = world_x_px / world_size_px * 360.0 - 180.0
    mercator_value = math.pi - 2.0 * math.pi * world_y_px / world_size_px
    latitude_deg = math.degrees(math.atan(math.sinh(mercator_value)))
    return longitude_deg, latitude_deg
