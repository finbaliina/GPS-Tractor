"""Generate or regenerate a route from a farmer-approved field boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from shapely.geometry import Polygon
from shapely.ops import unary_union

from contour_planning import plan_contour_route, save_contour_route_plan
from farm_data import field_dir, load_field_obstacles, read_boundary_points
from geometry_utils import clean_polygon
from json_io import read_json, write_json
from mapbox import mapbox_token_is_configured, metres_per_pixel
from route_planning import plan_route, save_route_plan
from route_visualisation import draw_route
from settings import settings
from terrain import get_terrain, load_saved_terrain


METRES_PER_PIXEL_KEYS = (
    "metres_per_pixel",
    "meters_per_pixel",
    "m_per_pixel",
)


def regenerate_field_route(farm_id: str, field_id: str) -> dict:
    """Rebuild a field route using its farmer-approved boundary.

    SAM is never called here. Terrain is fetched at most once when it is missing
    and setup is online; later regenerations use the saved local terrain files.
    """
    field_directory = field_dir(farm_id, field_id)
    satellite_path = field_directory / "satellite.png"
    metadata_path = field_directory / "metadata.json"
    approved_boundary_path = field_directory / "field_boundary_approved.json"

    _require_file(satellite_path, "This field has no saved satellite.png.")
    _require_file(metadata_path, "This field has no saved metadata.json.")
    _require_file(approved_boundary_path, "This field has no approved boundary.")

    approved_points = read_boundary_points(approved_boundary_path)
    try:
        field_polygon = clean_polygon(Polygon(approved_points))
    except ValueError as exc:
        raise RuntimeError("Approved field boundary is not a usable polygon.") from exc

    field_polygon = _apply_route_exclusions(
        field_polygon,
        load_field_obstacles(farm_id, field_id),
    )

    metadata = read_json(metadata_path)
    image_metres_per_pixel = _metres_per_pixel_from_metadata(metadata)

    _fetch_terrain_if_needed(
        field_directory=field_directory,
        satellite_path=satellite_path,
        metadata_path=metadata_path,
        metres_per_pixel=image_metres_per_pixel,
        field_polygon=field_polygon,
    )

    terrain_data = _load_terrain_if_available(field_directory)
    route_mode, route_plan = _plan_route_for_available_terrain(
        field_polygon=field_polygon,
        metres_per_pixel=image_metres_per_pixel,
        terrain_data=terrain_data,
    )

    route_plan_path = field_directory / "route_plan.json"
    if route_mode == "straight":
        save_route_plan(route_plan, route_plan_path)
    else:
        save_contour_route_plan(route_plan, route_plan_path)

    draw_route(
        satellite_path,
        field_polygon,
        route_plan,
        field_directory / "route_overlay.png",
    )
    _mark_route_current(field_directory, route_mode)

    return {
        "ok": True,
        "route_mode": route_mode,
        "route_overlay": "route_overlay.png",
        "passes": len(route_plan.swaths),
        "turns": int(route_plan.turns),
        "estimated_time_min": round(route_plan.estimated_time_s / 60.0, 1),
    }



def _apply_route_exclusions(field_polygon: Polygon, obstacles: list[dict]) -> Polygon:
    """Cut farmer-drawn obstacle areas out of the routeable field polygon."""
    obstacle_polygons = []
    for obstacle in obstacles:
        points = obstacle.get("points", []) if isinstance(obstacle, dict) else []
        if len(points) < 3:
            continue
        try:
            obstacle_polygon = clean_polygon(Polygon(points))
        except ValueError:
            continue
        clipped = obstacle_polygon.intersection(field_polygon)
        if not clipped.is_empty:
            obstacle_polygons.append(clipped)

    if not obstacle_polygons:
        return field_polygon

    routeable_geometry = field_polygon.difference(unary_union(obstacle_polygons))
    if routeable_geometry.is_empty:
        raise RuntimeError("The drawn exclusion areas cover the whole field.")
    try:
        return clean_polygon(routeable_geometry)
    except ValueError as exc:
        raise RuntimeError("The drawn exclusion areas leave no usable routeable field area.") from exc


def _fetch_terrain_if_needed(
    field_directory: Path,
    satellite_path: Path,
    metadata_path: Path,
    metres_per_pixel: float,
    field_polygon: Polygon,
) -> None:
    terrain_files_exist = (
        (field_directory / "terrain_data.npz").exists()
        and (field_directory / "terrain.json").exists()
    )
    if (
        terrain_files_exist
        or not settings.terrain.auto_fetch
        or not mapbox_token_is_configured()
    ):
        return

    capture = SimpleNamespace(
        folder=field_directory,
        image_path=satellite_path,
        metadata_path=metadata_path,
        metres_per_pixel=metres_per_pixel,
    )
    try:
        print("UI route: fetching terrain for this field once during setup...")
        get_terrain(capture, field_polygon)
    except Exception as exc:
        # Terrain is useful but not required for a valid straight route.
        print(
            "UI route: terrain fetch unavailable "
            f"({exc}); straight routing remains available."
        )


def _load_terrain_if_available(field_directory: Path):
    has_terrain_data = (field_directory / "terrain_data.npz").exists()
    has_terrain_summary = (field_directory / "terrain.json").exists()
    if not (has_terrain_data and has_terrain_summary):
        return None
    return load_saved_terrain(field_directory)


def _plan_route_for_available_terrain(
    field_polygon: Polygon,
    metres_per_pixel: float,
    terrain_data,
):
    slope_threshold_deg = settings.terrain.contour_slope_threshold_deg

    if terrain_data is None:
        print("UI route: no saved terrain data; using normal straight planner.")
        return "straight", plan_route(field_polygon, metres_per_pixel)

    p90_slope_deg = terrain_data.p90_slope_deg
    if p90_slope_deg >= slope_threshold_deg:
        print(
            f"UI route: terrain p90 slope {p90_slope_deg:.1f}° >= "
            f"{slope_threshold_deg:.1f}°; using straight contour planner."
        )
        contour_plan = plan_contour_route(
            field_polygon,
            terrain_data,
            metres_per_pixel,
        )
        return contour_plan.mode, contour_plan

    print(
        f"UI route: terrain p90 slope {p90_slope_deg:.1f}° < "
        f"{slope_threshold_deg:.1f}°; using normal straight planner."
    )
    return "straight", plan_route(field_polygon, metres_per_pixel)


def _metres_per_pixel_from_metadata(metadata: dict) -> float:
    """Return image scale, reconstructing it for older sequential fields when needed."""
    for container in (
        metadata,
        metadata.get("image", {}),
        metadata.get("mapbox", {}),
    ):
        for key in METRES_PER_PIXEL_KEYS:
            if key in container:
                return float(container[key])

    # Early human-in-the-loop/sequential field saves accidentally omitted the
    # explicit scale, but they did retain the Mapbox latitude and zoom.  The
    # scale is deterministic from those two values, so recover it rather than
    # forcing the farmer to rescan the field.
    position = metadata.get("position", {})
    image_settings = metadata.get("image_settings", {})
    latitude = position.get("latitude")
    zoom = metadata.get("zoom", image_settings.get("zoom"))
    if latitude is not None and zoom is not None:
        return float(metres_per_pixel(float(latitude), float(zoom)))

    raise ValueError(
        "This field does not contain enough saved map information to calculate "
        "its image scale. Rescan this field before generating a route."
    )


def _mark_route_current(field_directory: Path, route_mode: str) -> None:
    metadata_path = field_directory / "field.json"
    field_metadata = (
        read_json(metadata_path)
        if metadata_path.exists()
        else {"name": field_directory.name}
    )
    field_metadata["route_needs_regeneration"] = False
    field_metadata["route_mode"] = route_mode
    write_json(metadata_path, field_metadata)


def _require_file(path: Path, error_message: str) -> None:
    if not path.exists():
        raise FileNotFoundError(error_message)
