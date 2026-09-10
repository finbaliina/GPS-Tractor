"""One-off farm setup from a zoomed-out satellite overview.

The farmer searches by farm name/postcode, then draws one or more rough boxes over
all land they want the application to inspect. Those boxes are converted to a
GeoJSON search area and stored locally. No registry/cadastral data is used.
"""

from __future__ import annotations

from datetime import datetime, timezone
import shutil
from typing import Any

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from farm_data import FARMS_DIR, create_farm
from json_io import read_json, write_json
from mapbox import (
    fetch_satellite_image,
    forward_geocode,
    lonlat_to_world_pixel,
    metres_per_pixel,
    world_pixel_to_lonlat,
    zoom_for_radius,
)
from settings import PROJECT_ROOT, settings


SETUP_CACHE = PROJECT_ROOT / "setup_cache"


def geocode_farm(farm_name: str, postcode: str) -> dict[str, Any]:
    """Find a useful map centre for a farm name/postcode combination."""
    cleaned_farm_name = farm_name.strip()
    cleaned_postcode = postcode.strip().upper()

    if not cleaned_farm_name:
        raise ValueError("Enter the farm name.")
    if not cleaned_postcode:
        raise ValueError("Enter the farm postcode.")

    full_query = f"{cleaned_farm_name}, {cleaned_postcode}, United Kingdom"
    geocode_result = forward_geocode(full_query)

    # Rural farm names are not always present in geocoding data. A postcode-only
    # result is still useful because the farmer confirms the area on satellite view.
    if geocode_result is None:
        geocode_result = forward_geocode(f"{cleaned_postcode}, United Kingdom")
    if geocode_result is None:
        raise RuntimeError("Could not locate that farm/postcode with Mapbox.")

    coordinates = geocode_result.get("geometry", {}).get("coordinates") or []
    if len(coordinates) < 2:
        raise RuntimeError("Mapbox returned a result without coordinates.")

    longitude_deg = float(coordinates[0])
    latitude_deg = float(coordinates[1])
    properties = geocode_result.get("properties", {})
    display_label = (
        properties.get("full_address")
        or properties.get("name_preferred")
        or properties.get("name")
        or geocode_result.get("place_name")
        or full_query
    )

    return {
        "farm_name": cleaned_farm_name,
        "postcode": cleaned_postcode,
        "latitude": latitude_deg,
        "longitude": longitude_deg,
        "label": display_label,
    }


def build_setup_preview(farm_name: str, postcode: str) -> dict[str, Any]:
    """Create the zoomed-out image on which the farmer marks their farm areas."""
    farm_location = geocode_farm(farm_name, postcode)
    setup_settings = settings.farm_setup

    overview_zoom = zoom_for_radius(
        latitude_deg=farm_location["latitude"],
        radius_m=setup_settings.overview_radius_m,
        image_width_px=setup_settings.overview_width_px,
        image_height_px=setup_settings.overview_height_px,
    )

    preview_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    preview_directory = SETUP_CACHE / preview_id
    preview_directory.mkdir(parents=True, exist_ok=True)

    overview_image_path = preview_directory / "overview.png"
    fetch_satellite_image(
        latitude_deg=farm_location["latitude"],
        longitude_deg=farm_location["longitude"],
        zoom=overview_zoom,
        width_px=setup_settings.overview_width_px,
        height_px=setup_settings.overview_height_px,
        destination=overview_image_path,
    )

    overview_metres_per_pixel = metres_per_pixel(
        farm_location["latitude"],
        overview_zoom,
    )
    preview = {
        "id": preview_id,
        "location": farm_location,
        "map": {
            "width": setup_settings.overview_width_px,
            "height": setup_settings.overview_height_px,
            "zoom": overview_zoom,
            "centre_lat": farm_location["latitude"],
            "centre_lon": farm_location["longitude"],
            "approx_width_km": (
                setup_settings.overview_width_px * overview_metres_per_pixel / 1000.0
            ),
            "approx_height_km": (
                setup_settings.overview_height_px * overview_metres_per_pixel / 1000.0
            ),
        },
    }
    write_json(preview_directory / "setup.json", preview)
    return preview


def load_setup_preview(preview_id: str) -> dict[str, Any]:
    preview_path = SETUP_CACHE / preview_id / "setup.json"
    if not preview_path.exists():
        raise FileNotFoundError("Setup preview expired or was removed.")
    return read_json(preview_path)


def complete_setup(
    preview_id: str,
    rectangles: list[dict[str, float]],
) -> dict[str, Any]:
    """Save the farmer-selected rectangles as the permanent farm search area."""
    preview = load_setup_preview(preview_id)
    map_information = preview["map"]
    validated_rectangles = _validate_rectangles(rectangles, map_information)

    if not validated_rectangles:
        raise ValueError("Draw at least one box around the land you want to scan.")

    selected_polygons = [
        _rectangle_to_polygon(rectangle, map_information)
        for rectangle in validated_rectangles
    ]
    farm_search_geometry = unary_union(selected_polygons)
    if farm_search_geometry.is_empty:
        raise RuntimeError("The selected farm area could not be converted into a polygon.")

    farm_location = preview["location"]
    farm = create_farm(farm_location["farm_name"])
    farm_directory = FARMS_DIR / farm["id"]

    write_json(
        farm_directory / "farm_search_area.geojson",
        {
            "type": "Feature",
            "properties": {
                "purpose": "field discovery search area",
                "selection_method": "satellite_rectangles",
                "area_count": len(validated_rectangles),
            },
            "geometry": mapping(farm_search_geometry),
        },
    )

    farm_metadata_path = farm_directory / "farm.json"
    farm_metadata = read_json(farm_metadata_path)
    farm_metadata.update(
        {
            "postcode": farm_location["postcode"],
            "setup_complete": True,
            "setup_method": "satellite_rectangles",
            "setup_location": {
                "latitude": farm_location["latitude"],
                "longitude": farm_location["longitude"],
            },
            "search_area_count": len(validated_rectangles),
            "overview_map": map_information,
        }
    )
    write_json(farm_metadata_path, farm_metadata)

    temporary_overview_path = SETUP_CACHE / preview_id / "overview.png"
    if temporary_overview_path.exists():
        shutil.copy2(temporary_overview_path, farm_directory / "overview.png")

    # The preview is disposable once its geometry/image have been copied locally.
    shutil.rmtree(SETUP_CACHE / preview_id, ignore_errors=True)
    return farm


def _rectangle_to_polygon(
    rectangle: dict[str, float],
    map_information: dict[str, Any],
) -> Polygon:
    left_px, right_px = sorted((rectangle["x1"], rectangle["x2"]))
    top_px, bottom_px = sorted((rectangle["y1"], rectangle["y2"]))

    north_west = pixel_to_lonlat(left_px, top_px, map_information)
    north_east = pixel_to_lonlat(right_px, top_px, map_information)
    south_east = pixel_to_lonlat(right_px, bottom_px, map_information)
    south_west = pixel_to_lonlat(left_px, bottom_px, map_information)
    return Polygon([north_west, north_east, south_east, south_west, north_west])


def _validate_rectangles(
    rectangles: list[dict[str, float]],
    map_information: dict[str, Any],
) -> list[dict[str, float]]:
    image_width_px = float(map_information["width"])
    image_height_px = float(map_information["height"])
    minimum_box_size_px = settings.farm_setup.minimum_selection_box_px
    validated_rectangles: list[dict[str, float]] = []

    for rectangle in rectangles:
        try:
            left_x = _clamp(float(rectangle["x1"]), 0.0, image_width_px)
            top_y = _clamp(float(rectangle["y1"]), 0.0, image_height_px)
            right_x = _clamp(float(rectangle["x2"]), 0.0, image_width_px)
            bottom_y = _clamp(float(rectangle["y2"]), 0.0, image_height_px)
        except (KeyError, TypeError, ValueError):
            continue

        if (
            abs(right_x - left_x) < minimum_box_size_px
            or abs(bottom_y - top_y) < minimum_box_size_px
        ):
            continue

        validated_rectangles.append(
            {"x1": left_x, "y1": top_y, "x2": right_x, "y2": bottom_y}
        )

    return validated_rectangles


def pixel_to_lonlat(
    image_x_px: float,
    image_y_px: float,
    map_information: dict[str, Any],
) -> tuple[float, float]:
    zoom = float(map_information["zoom"])
    image_width_px = float(map_information["width"])
    image_height_px = float(map_information["height"])

    centre_world_x, centre_world_y = lonlat_to_world_pixel(
        map_information["centre_lon"],
        map_information["centre_lat"],
        zoom,
    )
    point_world_x = centre_world_x + image_x_px - image_width_px / 2.0
    point_world_y = centre_world_y + image_y_px - image_height_px / 2.0
    return world_pixel_to_lonlat(point_world_x, point_world_y, zoom)


def lonlat_to_pixel(
    longitude_deg: float,
    latitude_deg: float,
    map_information: dict[str, Any],
) -> list[float]:
    zoom = float(map_information["zoom"])
    image_width_px = float(map_information["width"])
    image_height_px = float(map_information["height"])

    centre_world_x, centre_world_y = lonlat_to_world_pixel(
        map_information["centre_lon"],
        map_information["centre_lat"],
        zoom,
    )
    point_world_x, point_world_y = lonlat_to_world_pixel(
        longitude_deg,
        latitude_deg,
        zoom,
    )
    return [
        point_world_x - centre_world_x + image_width_px / 2.0,
        point_world_y - centre_world_y + image_height_px / 2.0,
    ]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


# Backwards-compatible name used by field_discovery in previous project versions.
fetch_mapbox_satellite = fetch_satellite_image
