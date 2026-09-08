from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from shapely.geometry import Polygon

from farm_data import field_dir, read_boundary_points
from route_planning import plan_route, save_route_plan
from contour_planning import plan_contour_route, save_contour_route_plan
from route_visualisation import draw_route


def regenerate_field_route(farm_id: str, field_id: str) -> dict:
    """
    Rebuild a field route using the farmer-approved boundary.

    This never calls SAM. If terrain has not yet been saved and internet is
    available during setup, it can fetch terrain once; later regenerations use
    the local terrain files. If terrain cannot be fetched, straight routing still works.
    """
    path = field_dir(farm_id, field_id)

    satellite_path = path / "satellite.png"
    metadata_path = path / "metadata.json"
    terrain_data_path = path / "terrain_data.npz"
    terrain_json_path = path / "terrain.json"
    approved_path = path / "field_boundary_approved.json"

    if not satellite_path.exists():
        raise FileNotFoundError("This field has no saved satellite.png.")

    if not metadata_path.exists():
        raise FileNotFoundError("This field has no saved metadata.json.")

    if not approved_path.exists():
        raise FileNotFoundError("This field has no approved boundary.")

    points = read_boundary_points(approved_path)

    polygon = Polygon(points)

    if not polygon.is_valid:
        polygon = polygon.buffer(0)

    if polygon.is_empty:
        raise RuntimeError("Approved field boundary is not a usable polygon.")

    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda p: p.area)

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metres_per_pixel = _metres_per_pixel(metadata)

    threshold = float(os.getenv("CONTOUR_SLOPE_THRESHOLD_DEG", "5.0"))

    use_contour = False
    terrain = None
    p90_slope = None

    if (
        not (terrain_data_path.exists() and terrain_json_path.exists())
        and _env_bool("AUTO_FETCH_TERRAIN", True)
        and os.getenv("MAPBOX_TOKEN", "").strip()
    ):
        try:
            from terrain import get_terrain

            capture = SimpleNamespace(
                folder=path,
                image_path=satellite_path,
                metadata_path=metadata_path,
                metres_per_pixel=metres_per_pixel,
            )
            print("UI route: fetching terrain for this field once during setup...")
            get_terrain(capture, polygon)
        except Exception as exc:
            print(f"UI route: terrain fetch unavailable ({exc}); using straight routing if needed.")

    if terrain_data_path.exists() and terrain_json_path.exists():
        terrain = _load_saved_terrain(
            terrain_data_path=terrain_data_path,
            terrain_json_path=terrain_json_path,
        )

        p90_slope = float(terrain.p90_slope_deg)
        use_contour = p90_slope >= threshold

    if use_contour:
        print(
            f"UI route: terrain p90 slope {p90_slope:.1f}° >= "
            f"{threshold:.1f}°; using straight contour planner."
        )
        plan = plan_contour_route(
            polygon,
            terrain,
            metres_per_pixel,
        )
        save_contour_route_plan(
            plan,
            path / "route_plan.json",
        )
        route_mode = getattr(plan, "mode", "contour")
    else:
        if p90_slope is None:
            print("UI route: no saved terrain data; using normal straight planner.")
        else:
            print(
                f"UI route: terrain p90 slope {p90_slope:.1f}° < "
                f"{threshold:.1f}°; using normal straight planner."
            )

        plan = plan_route(
            polygon,
            metres_per_pixel,
        )
        save_route_plan(
            plan,
            path / "route_plan.json",
        )
        route_mode = "straight"

    draw_route(
        satellite_path,
        polygon,
        plan,
        path / "route_overlay.png",
    )

    _mark_route_current(
        path=path,
        route_mode=route_mode,
    )

    return {
        "ok": True,
        "route_mode": route_mode,
        "route_overlay": "route_overlay.png",
        "passes": len(plan.swaths),
        "turns": int(plan.turns),
        "estimated_time_min": round(float(plan.estimated_time_s) / 60.0, 1),
    }


def _load_saved_terrain(
    terrain_data_path: Path,
    terrain_json_path: Path,
):
    data = np.load(terrain_data_path)
    summary = json.loads(terrain_json_path.read_text(encoding="utf-8"))

    # This object deliberately exposes the same attributes used by
    # contour_planning.py without downloading terrain again.
    return SimpleNamespace(
        elevation_grid=np.asarray(data["elevation_m"], dtype=np.float32),
        slope_grid_deg=np.asarray(data["slope_deg"], dtype=np.float32),
        field_mask=np.asarray(data["field_mask"], dtype=bool),
        image_x=np.asarray(data["image_x"], dtype=float),
        image_y=np.asarray(data["image_y"], dtype=float),
        mean_slope_deg=float(summary.get("mean_slope_deg", 0.0)),
        median_slope_deg=float(summary.get("median_slope_deg", 0.0)),
        p90_slope_deg=float(summary.get("p90_slope_deg", 0.0)),
        max_slope_deg=float(summary.get("max_slope_deg", 0.0)),
        min_elevation_m=float(summary.get("min_elevation_m", 0.0)),
        max_elevation_m=float(summary.get("max_elevation_m", 0.0)),
        contour_interval_m=float(summary.get("contour_interval_m", 2.0)),
        terrain_json_path=terrain_json_path,
        data_path=terrain_data_path,
    )


def _metres_per_pixel(metadata: dict) -> float:
    for key in (
        "metres_per_pixel",
        "meters_per_pixel",
        "m_per_pixel",
    ):
        if key in metadata:
            return float(metadata[key])

    image = metadata.get("image", {})
    for key in (
        "metres_per_pixel",
        "meters_per_pixel",
        "m_per_pixel",
    ):
        if key in image:
            return float(image[key])

    mapbox = metadata.get("mapbox", {})
    for key in (
        "metres_per_pixel",
        "meters_per_pixel",
        "m_per_pixel",
    ):
        if key in mapbox:
            return float(mapbox[key])

    raise ValueError(
        "metadata.json does not contain metres_per_pixel. "
        "Re-import a capture generated by the current satellite_image.py."
    )


def _mark_route_current(path: Path, route_mode: str) -> None:
    field_json = path / "field.json"

    if field_json.exists():
        data = json.loads(field_json.read_text(encoding="utf-8"))
    else:
        data = {"name": path.name}

    data["route_needs_regeneration"] = False
    data["route_mode"] = route_mode

    field_json.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
