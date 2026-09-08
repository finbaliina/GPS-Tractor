from __future__ import annotations

"""One-off farm setup from a zoomed-out satellite overview.

The farmer searches by farm name/postcode, then draws one or more rough boxes over
all land they want the application to inspect.  Those boxes are converted to a
GeoJSON search area and stored locally.  No registry/cadastral data is used.
"""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any
from urllib.parse import quote

import requests
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from farm_data import FARMS_DIR, create_farm


EARTH_CIRCUMFERENCE_M = 40075016.68557849
MAPBOX_TILE_SIZE = 512
SETUP_CACHE = Path(__file__).resolve().parent / "setup_cache"


def geocode_farm(farm_name: str, postcode: str) -> dict[str, Any]:
    token = os.getenv("MAPBOX_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MAPBOX_TOKEN is missing from .env")

    farm_name = farm_name.strip()
    postcode = postcode.strip().upper()
    if not farm_name:
        raise ValueError("Enter the farm name.")
    if not postcode:
        raise ValueError("Enter the farm postcode.")

    query = f"{farm_name}, {postcode}, United Kingdom"
    result = _mapbox_forward_geocode(query, token)

    # Rural farm names are not always present in geocoding data.  The postcode
    # fallback is still good enough because the farmer confirms the location on
    # the satellite overview before anything is saved.
    if result is None:
        result = _mapbox_forward_geocode(f"{postcode}, United Kingdom", token)
    if result is None:
        raise RuntimeError("Could not locate that farm/postcode with Mapbox.")

    coords = result.get("geometry", {}).get("coordinates") or []
    if len(coords) < 2:
        raise RuntimeError("Mapbox returned a result without coordinates.")

    lon, lat = float(coords[0]), float(coords[1])
    props = result.get("properties", {})
    label = (
        props.get("full_address")
        or props.get("name_preferred")
        or props.get("name")
        or result.get("place_name")
        or query
    )

    return {
        "farm_name": farm_name,
        "postcode": postcode,
        "latitude": lat,
        "longitude": lon,
        "label": label,
    }


def build_setup_preview(farm_name: str, postcode: str) -> dict[str, Any]:
    location = geocode_farm(farm_name, postcode)

    # Deliberately broad: this is only for identifying the farm area.  Detailed
    # imagery is fetched later for field discovery.
    radius_m = float(os.getenv("FARM_OVERVIEW_RADIUS_M", "6500"))
    width = int(os.getenv("FARM_OVERVIEW_WIDTH", "1200"))
    height = int(os.getenv("FARM_OVERVIEW_HEIGHT", "900"))
    zoom = zoom_for_radius(location["latitude"], radius_m, width, height)

    preview_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    preview_dir = SETUP_CACHE / preview_id
    preview_dir.mkdir(parents=True, exist_ok=True)
    image_path = preview_dir / "overview.png"

    fetch_mapbox_satellite(
        latitude=location["latitude"],
        longitude=location["longitude"],
        zoom=zoom,
        width=width,
        height=height,
        destination=image_path,
    )

    payload = {
        "id": preview_id,
        "location": location,
        "map": {
            "width": width,
            "height": height,
            "zoom": zoom,
            "centre_lat": location["latitude"],
            "centre_lon": location["longitude"],
            "approx_width_km": width * metres_per_pixel(location["latitude"], zoom) / 1000.0,
            "approx_height_km": height * metres_per_pixel(location["latitude"], zoom) / 1000.0,
        },
    }
    (preview_dir / "setup.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def load_setup_preview(preview_id: str) -> dict[str, Any]:
    path = SETUP_CACHE / preview_id / "setup.json"
    if not path.exists():
        raise FileNotFoundError("Setup preview expired or was removed.")
    return json.loads(path.read_text(encoding="utf-8"))


def complete_setup(preview_id: str, rectangles: list[dict[str, float]]) -> dict[str, Any]:
    preview = load_setup_preview(preview_id)
    map_info = preview["map"]
    clean_rects = _validate_rectangles(rectangles, map_info)
    if not clean_rects:
        raise ValueError("Draw at least one box around the land you want to scan.")

    polygons = []
    for rect in clean_rects:
        x1, x2 = sorted((rect["x1"], rect["x2"]))
        y1, y2 = sorted((rect["y1"], rect["y2"]))
        nw = pixel_to_lonlat(x1, y1, map_info)
        ne = pixel_to_lonlat(x2, y1, map_info)
        se = pixel_to_lonlat(x2, y2, map_info)
        sw = pixel_to_lonlat(x1, y2, map_info)
        polygons.append(Polygon([nw, ne, se, sw, nw]))

    search_geom = unary_union(polygons)
    if search_geom.is_empty:
        raise RuntimeError("The selected farm area could not be converted into a polygon.")

    loc = preview["location"]
    farm = create_farm(loc["farm_name"])
    farm_dir = FARMS_DIR / farm["id"]

    search_geojson = {
        "type": "Feature",
        "properties": {
            "purpose": "field discovery search area",
            "selection_method": "satellite_rectangles",
            "area_count": len(clean_rects),
        },
        "geometry": mapping(search_geom),
    }
    (farm_dir / "farm_search_area.geojson").write_text(
        json.dumps(search_geojson, indent=2) + "\n", encoding="utf-8"
    )

    farm_json = farm_dir / "farm.json"
    metadata = json.loads(farm_json.read_text(encoding="utf-8"))
    metadata.update({
        "postcode": loc["postcode"],
        "setup_complete": True,
        "setup_method": "satellite_rectangles",
        "setup_location": {
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
        },
        "search_area_count": len(clean_rects),
        "overview_map": map_info,
    })
    farm_json.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    source_image = SETUP_CACHE / preview_id / "overview.png"
    if source_image.exists():
        shutil.copy2(source_image, farm_dir / "overview.png")

    # The preview is disposable once the selected geometry and image have been
    # copied into the farm folder.
    shutil.rmtree(SETUP_CACHE / preview_id, ignore_errors=True)
    return farm


def _validate_rectangles(
    rectangles: list[dict[str, float]], map_info: dict[str, Any]
) -> list[dict[str, float]]:
    width = float(map_info["width"])
    height = float(map_info["height"])
    result = []

    for item in rectangles:
        try:
            x1 = max(0.0, min(width, float(item["x1"])))
            y1 = max(0.0, min(height, float(item["y1"])))
            x2 = max(0.0, min(width, float(item["x2"])))
            y2 = max(0.0, min(height, float(item["y2"])))
        except (KeyError, TypeError, ValueError):
            continue

        # Ignore accidental clicks/tiny drags.
        if abs(x2 - x1) < 8 or abs(y2 - y1) < 8:
            continue
        result.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    return result


def pixel_to_lonlat(x: float, y: float, map_info: dict[str, Any]) -> tuple[float, float]:
    zoom = float(map_info["zoom"])
    width = float(map_info["width"])
    height = float(map_info["height"])
    cx, cy = _world_pixel(map_info["centre_lon"], map_info["centre_lat"], zoom)
    wx = cx + float(x) - width / 2.0
    wy = cy + float(y) - height / 2.0
    return _world_pixel_to_lonlat(wx, wy, zoom)


def lonlat_to_pixel(lon: float, lat: float, map_info: dict[str, Any]) -> list[float]:
    zoom = float(map_info["zoom"])
    width = float(map_info["width"])
    height = float(map_info["height"])
    cx, cy = _world_pixel(map_info["centre_lon"], map_info["centre_lat"], zoom)
    px, py = _world_pixel(lon, lat, zoom)
    return [px - cx + width / 2.0, py - cy + height / 2.0]


def _world_pixel(lon: float, lat: float, zoom: float) -> tuple[float, float]:
    world = MAPBOX_TILE_SIZE * 2**zoom
    x = (lon + 180.0) / 360.0 * world
    sin_lat = math.sin(math.radians(max(min(lat, 85.05112878), -85.05112878)))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * world
    return x, y


def _world_pixel_to_lonlat(x: float, y: float, zoom: float) -> tuple[float, float]:
    world = MAPBOX_TILE_SIZE * 2**zoom
    lon = x / world * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / world
    lat = math.degrees(math.atan(math.sinh(n)))
    return lon, lat


def zoom_for_radius(latitude: float, radius_m: float, width: int, height: int) -> float:
    metres_across = radius_m * 2.0
    limiting_pixels = min(width, height)
    wanted_mpp = metres_across / limiting_pixels
    zoom = math.log2(
        EARTH_CIRCUMFERENCE_M * math.cos(math.radians(latitude))
        / (MAPBOX_TILE_SIZE * wanted_mpp)
    )
    return max(1.0, min(18.0, zoom))


def metres_per_pixel(latitude: float, zoom: float) -> float:
    return (
        EARTH_CIRCUMFERENCE_M
        * math.cos(math.radians(latitude))
        / (MAPBOX_TILE_SIZE * 2**zoom)
    )


def fetch_mapbox_satellite(
    latitude: float,
    longitude: float,
    zoom: float,
    width: int,
    height: int,
    destination: Path,
) -> None:
    token = os.getenv("MAPBOX_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MAPBOX_TOKEN is missing from .env")
    centre = f"{longitude:.8f},{latitude:.8f},{zoom:.3f},0"
    url = (
        "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/"
        f"{centre}/{width}x{height}?access_token={quote(token, safe='')}"
    )
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)


def _mapbox_forward_geocode(query: str, token: str) -> dict[str, Any] | None:
    url = "https://api.mapbox.com/search/geocode/v6/forward"
    params = {
        "q": query,
        "country": "gb",
        "limit": 1,
        "access_token": token,
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    features = response.json().get("features", [])
    return features[0] if features else None
