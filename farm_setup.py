from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

from cadastral_lookup import find_candidate_parcels
from farm_data import FARMS_DIR, create_farm


EARTH_CIRCUMFERENCE_M = 40075016.68557849
MAPBOX_TILE_SIZE = 512


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

    # Rural farm names are not always recognised.  Falling back to postcode
    # gets us into the correct small area and the cadastral polygons do the
    # remainder of the location work.
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
    radius_m = float(os.getenv("FARM_LOOKUP_RADIUS_M", "4500"))
    parcels = find_candidate_parcels(
        location["latitude"],
        location["longitude"],
        search_radius_m=radius_m,
    )

    # Whole-farm candidate stage intentionally shows a broad area.  It is only
    # an overview; detailed imagery is fetched later for individual fields.
    width = int(os.getenv("FARM_OVERVIEW_WIDTH", "1200"))
    height = int(os.getenv("FARM_OVERVIEW_HEIGHT", "900"))
    zoom = zoom_for_radius(location["latitude"], radius_m, width, height)

    preview_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    preview_dir = Path("setup_cache") / preview_id
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
        "parcels": parcels,
        "map": {
            "width": width,
            "height": height,
            "zoom": zoom,
            "centre_lat": location["latitude"],
            "centre_lon": location["longitude"],
        },
    }
    (preview_dir / "setup.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def load_setup_preview(preview_id: str) -> dict[str, Any]:
    path = Path("setup_cache") / preview_id / "setup.json"
    if not path.exists():
        raise FileNotFoundError("Setup preview expired or was removed.")
    return json.loads(path.read_text(encoding="utf-8"))


def complete_setup(preview_id: str, selected_ids: list[str]) -> dict[str, Any]:
    preview = load_setup_preview(preview_id)
    selected_set = set(selected_ids)
    selected = [p for p in preview["parcels"] if p["id"] in selected_set]
    if not selected:
        raise ValueError("Select at least one cadastral parcel.")

    loc = preview["location"]
    farm = create_farm(loc["farm_name"])
    farm_dir = FARMS_DIR / farm["id"]

    selected_geoms = [shape(p["geometry"]) for p in selected]
    selected_union = unary_union(selected_geoms)

    # Buffer is done in metres in British National Grid.
    try:
        import geopandas as gpd
        selection_frame = gpd.GeoDataFrame(
            {"parcel_id": [p["id"] for p in selected]},
            geometry=selected_geoms,
            crs="EPSG:4326",
        )
        selection_bng = selection_frame.to_crs("EPSG:27700")
        union_bng = unary_union(list(selection_bng.geometry))
        buffer_m = float(os.getenv("FARM_EXTRA_VIEW_BUFFER_M", "800"))
        search_bng = union_bng.buffer(buffer_m)
        search_frame = gpd.GeoSeries([search_bng], crs="EPSG:27700").to_crs("EPSG:4326")
        farm_search_area = search_frame.iloc[0]
    except ImportError:
        # Setup with real cadastral data already requires GeoPandas, but keeping
        # a clear error here is nicer than silently using a degree buffer.
        raise RuntimeError(
            "GeoPandas is required to save the buffered farm search area. "
            "Run: python -m pip install geopandas pyogrio"
        )

    selection_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "parcel_id": p["id"],
                    "source": p["source"],
                    "area_m2": p["area_m2"],
                },
                "geometry": p["geometry"],
            }
            for p in selected
        ],
    }
    (farm_dir / "cadastral_selection.geojson").write_text(
        json.dumps(selection_geojson, indent=2) + "\n", encoding="utf-8"
    )

    search_geojson = {
        "type": "Feature",
        "properties": {
            "purpose": "field discovery search area",
            "buffer_m": float(os.getenv("FARM_EXTRA_VIEW_BUFFER_M", "800")),
        },
        "geometry": mapping(farm_search_area),
    }
    (farm_dir / "farm_search_area.geojson").write_text(
        json.dumps(search_geojson, indent=2) + "\n", encoding="utf-8"
    )

    # Store only what the tractor needs after setup.  No registry re-query is
    # required to open the farm or add fields later.
    farm_json = farm_dir / "farm.json"
    metadata = json.loads(farm_json.read_text(encoding="utf-8"))
    metadata.update({
        "postcode": loc["postcode"],
        "setup_complete": True,
        "setup_location": {
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
        },
        "cadastral_parcel_count": len(selected),
        "cadastral_sources": sorted({p["source"] for p in selected}),
        "registry_lookup_complete": True,
        "registry_lookup_required_again": False,
    })
    farm_json.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    # Keep the setup satellite image locally as a convenient overview.
    source_image = Path("setup_cache") / preview_id / "overview.png"
    if source_image.exists():
        (farm_dir / "overview.png").write_bytes(source_image.read_bytes())

    return farm


def polygon_to_pixels(geometry: dict[str, Any], map_info: dict[str, Any]) -> list[list[list[float]]]:
    geom = shape(geometry)
    polygons = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    rings = []
    for polygon in polygons:
        rings.append([
            lonlat_to_pixel(lon, lat, map_info)
            for lon, lat in polygon.exterior.coords
        ])
    return rings


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


def zoom_for_radius(latitude: float, radius_m: float, width: int, height: int) -> float:
    metres_across = radius_m * 2.0
    limiting_pixels = min(width, height)
    wanted_mpp = metres_across / limiting_pixels
    zoom = math.log2(
        EARTH_CIRCUMFERENCE_M * math.cos(math.radians(latitude))
        / (MAPBOX_TILE_SIZE * wanted_mpp)
    )
    return max(1.0, min(18.0, zoom))


def fetch_mapbox_satellite(latitude: float, longitude: float, zoom: float, width: int, height: int, destination: Path) -> None:
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
