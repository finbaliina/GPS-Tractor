"""Local farm, field and boundary storage helpers."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from json_io import read_json, write_json
from settings import PROJECT_ROOT


FARMS_DIR = PROJECT_ROOT / "farms"
CAPTURES_DIR = PROJECT_ROOT / "captures"

COORDINATE_EQUALITY_TOLERANCE = 1e-9
DUPLICATE_SUFFIX_START = 2

CAPTURE_FILES_TO_COPY = (
    "satellite.png",
    "metadata.json",
    "field_boundary.png",
    "field_boundary.json",
    "terrain_data.npz",
    "terrain.json",
    "terrain_overlay.png",
    "route_plan.json",
    "route_overlay.png",
    "mask.png",
)

BOUNDARY_POINT_KEYS = (
    "points",
    "pixel_points",
    "polygon_pixels",
    "boundary_pixels",
    "contour_points",
    "vertices",
    "coordinates",
)


def ensure_storage() -> None:
    FARMS_DIR.mkdir(exist_ok=True)
    CAPTURES_DIR.mkdir(exist_ok=True)


def slugify(text: str) -> str:
    cleaned_text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip())
    cleaned_text = cleaned_text.strip("_").lower()
    return cleaned_text or "field"


def unique_directory(parent: Path, base_name: str) -> Path:
    """Return an unused child directory path without creating it."""
    candidate_path = parent / base_name
    if not candidate_path.exists():
        return candidate_path

    suffix_number = DUPLICATE_SUFFIX_START
    while (parent / f"{base_name}_{suffix_number}").exists():
        suffix_number += 1
    return parent / f"{base_name}_{suffix_number}"


def list_farms() -> list[dict[str, Any]]:
    ensure_storage()
    farms: list[dict[str, Any]] = []

    for farm_directory in sorted(path for path in FARMS_DIR.iterdir() if path.is_dir()):
        metadata_path = farm_directory / "farm.json"
        metadata = (
            read_json(metadata_path)
            if metadata_path.exists()
            else {"name": farm_directory.name}
        )

        fields_directory = farm_directory / "fields"
        field_count = (
            sum(1 for path in fields_directory.iterdir() if path.is_dir())
            if fields_directory.exists()
            else 0
        )

        farms.append(
            {
                "id": farm_directory.name,
                "name": metadata.get("name", farm_directory.name),
                "field_count": field_count,
                "setup_complete": bool(metadata.get("setup_complete", False)),
            }
        )

    return farms


def delete_farm(farm_id: str) -> None:
    """Permanently remove a farm and all locally stored data beneath it."""
    farm_directory = FARMS_DIR / farm_id
    if not farm_directory.exists() or not farm_directory.is_dir():
        raise FileNotFoundError(farm_id)
    shutil.rmtree(farm_directory)


def create_farm(name: str) -> dict[str, str]:
    ensure_storage()
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("Farm name cannot be empty.")

    farm_directory = unique_directory(FARMS_DIR, slugify(cleaned_name))
    (farm_directory / "fields").mkdir(parents=True)
    write_json(farm_directory / "farm.json", {"name": cleaned_name})
    return {"id": farm_directory.name, "name": cleaned_name}


def get_farm(farm_id: str) -> dict[str, Any]:
    farm_directory = FARMS_DIR / farm_id
    if not farm_directory.exists():
        raise FileNotFoundError(farm_id)

    metadata = read_json(farm_directory / "farm.json")
    fields_directory = farm_directory / "fields"
    fields_directory.mkdir(exist_ok=True)

    fields: list[dict[str, Any]] = []
    for field_directory in sorted(
        path for path in fields_directory.iterdir() if path.is_dir()
    ):
        field_metadata_path = field_directory / "field.json"
        field_metadata = (
            read_json(field_metadata_path)
            if field_metadata_path.exists()
            else {"name": field_directory.name}
        )
        fields.append(
            {
                "id": field_directory.name,
                "name": field_metadata.get("name", field_directory.name),
                "route_ready": (field_directory / "route_plan.json").exists(),
                "has_terrain": (field_directory / "terrain_data.npz").exists(),
                "boundary_source": field_metadata.get("boundary_source", "unknown"),
            }
        )

    return {
        "id": farm_id,
        "name": metadata.get("name", farm_id),
        "postcode": metadata.get("postcode"),
        "setup_complete": bool(metadata.get("setup_complete", False)),
        "setup_method": metadata.get("setup_method"),
        "search_area_count": int(metadata.get("search_area_count", 0)),
        "fields": fields,
    }


def list_capture_candidates() -> list[dict[str, str]]:
    ensure_storage()
    available_captures: list[dict[str, str]] = []

    capture_directories = sorted(
        (path for path in CAPTURES_DIR.iterdir() if path.is_dir()),
        reverse=True,
    )
    for capture_directory in capture_directories:
        has_satellite_image = (capture_directory / "satellite.png").exists()
        has_boundary = (capture_directory / "field_boundary.json").exists()
        if has_satellite_image and has_boundary:
            available_captures.append(
                {"id": capture_directory.name, "label": capture_directory.name}
            )

    return available_captures


def import_capture_as_field(
    farm_id: str,
    capture_id: str,
    field_name: str,
) -> dict[str, str]:
    farm_directory = FARMS_DIR / farm_id
    capture_directory = CAPTURES_DIR / capture_id

    if not farm_directory.exists():
        raise FileNotFoundError(f"Farm not found: {farm_id}")
    if not capture_directory.exists():
        raise FileNotFoundError(f"Capture not found: {capture_id}")
    if not (capture_directory / "satellite.png").exists():
        raise FileNotFoundError("Capture does not contain satellite.png")
    if not (capture_directory / "field_boundary.json").exists():
        raise FileNotFoundError("Capture does not contain field_boundary.json")

    cleaned_field_name = field_name.strip()
    if not cleaned_field_name:
        raise ValueError("Field name cannot be empty.")

    fields_directory = farm_directory / "fields"
    fields_directory.mkdir(exist_ok=True)
    new_field_directory = unique_directory(
        fields_directory,
        slugify(cleaned_field_name),
    )
    new_field_directory.mkdir()

    for filename in CAPTURE_FILES_TO_COPY:
        source_path = capture_directory / filename
        if source_path.exists():
            shutil.copy2(source_path, new_field_directory / filename)

    detected_boundary_path = new_field_directory / "field_boundary_detected.json"
    shutil.copy2(
        capture_directory / "field_boundary.json",
        detected_boundary_path,
    )
    detected_points = read_boundary_points(detected_boundary_path)
    write_boundary_points_compatible(
        new_field_directory / "field_boundary_approved.json",
        detected_points,
        source="cv",
    )

    write_json(
        new_field_directory / "field.json",
        {
            "name": cleaned_field_name,
            "source_capture": capture_id,
            "boundary_source": "cv",
            "boundary_locked": False,
            "route_needs_regeneration": False,
        },
    )

    return {"id": new_field_directory.name, "name": cleaned_field_name}


def field_dir(farm_id: str, field_id: str) -> Path:
    field_directory = FARMS_DIR / farm_id / "fields" / field_id
    if not field_directory.exists():
        raise FileNotFoundError(field_id)
    return field_directory


def get_field(farm_id: str, field_id: str) -> dict[str, Any]:
    field_directory = field_dir(farm_id, field_id)
    metadata_path = field_directory / "field.json"
    field_metadata = (
        read_json(metadata_path)
        if metadata_path.exists()
        else {
            "name": field_id,
            "boundary_source": "cv",
            "boundary_locked": False,
        }
    )

    detected_boundary_path = field_directory / "field_boundary_detected.json"
    current_boundary_path = field_directory / "field_boundary.json"
    approved_boundary_path = field_directory / "field_boundary_approved.json"

    # Older fields may predate the detected/approved split. Preserve the current
    # boundary as the immutable detected baseline when upgrading them.
    if not detected_boundary_path.exists():
        if not current_boundary_path.exists():
            raise FileNotFoundError(f"No field boundary found for {field_id}.")
        shutil.copy2(current_boundary_path, detected_boundary_path)

    detected_points = read_boundary_points(detected_boundary_path)
    approved_points = _load_best_approved_points(
        approved_boundary_path=approved_boundary_path,
        current_boundary_path=current_boundary_path,
        detected_points=detected_points,
    )

    if not approved_boundary_path.exists():
        write_boundary_points_compatible(
            approved_boundary_path,
            approved_points,
            source=field_metadata.get("boundary_source", "cv"),
        )

    return {
        "id": field_id,
        "name": field_metadata.get("name", field_id),
        "boundary_source": field_metadata.get("boundary_source", "cv"),
        "boundary_locked": bool(field_metadata.get("boundary_locked", False)),
        "points": approved_points,
        "detected_points": detected_points,
        "route_ready": (field_directory / "route_plan.json").exists(),
        "source_candidate": field_metadata.get("source_candidate"),
    }


def _load_best_approved_points(
    approved_boundary_path: Path,
    current_boundary_path: Path,
    detected_points: list[list[float]],
) -> list[list[float]]:
    try:
        if approved_boundary_path.exists():
            return _extract_points(read_json(approved_boundary_path))
        if current_boundary_path.exists():
            return read_boundary_points(current_boundary_path)
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    return detected_points


def save_boundary(
    farm_id: str,
    field_id: str,
    points: list[list[float]],
    source: str = "manual_edit",
) -> None:
    cleaned_points = _strip_closed_coordinate(points)
    field_directory = field_dir(farm_id, field_id)

    write_boundary_points_compatible(
        field_directory / "field_boundary_approved.json",
        cleaned_points,
        source=source,
    )
    write_boundary_points_compatible(
        field_directory / "field_boundary.json",
        cleaned_points,
        source=source,
    )

    metadata_path = field_directory / "field.json"
    field_metadata = (
        read_json(metadata_path)
        if metadata_path.exists()
        else {"name": field_id}
    )
    field_metadata.update(
        {
            "boundary_source": source,
            "boundary_locked": True,
            "route_needs_regeneration": True,
        }
    )
    write_json(metadata_path, field_metadata)


def reset_boundary(farm_id: str, field_id: str) -> list[list[float]]:
    field_directory = field_dir(farm_id, field_id)
    detected_points = read_boundary_points(
        field_directory / "field_boundary_detected.json"
    )

    for filename in ("field_boundary_approved.json", "field_boundary.json"):
        write_boundary_points_compatible(
            field_directory / filename,
            detected_points,
            source="cv",
        )

    metadata_path = field_directory / "field.json"
    field_metadata = (
        read_json(metadata_path)
        if metadata_path.exists()
        else {"name": field_id}
    )
    field_metadata.update(
        {
            "boundary_source": "cv",
            "boundary_locked": False,
            "route_needs_regeneration": True,
        }
    )
    write_json(metadata_path, field_metadata)
    return detected_points


def read_boundary_points(path: Path) -> list[list[float]]:
    return _extract_points(read_json(path))


def _extract_points(data: Any) -> list[list[float]]:
    """Read every boundary JSON format used by earlier GPS Tractor versions."""
    if isinstance(data, list):
        return _normalise_coordinate_container(data)
    if not isinstance(data, dict):
        raise ValueError("Boundary JSON is neither an object nor a list.")

    for point_key in BOUNDARY_POINT_KEYS:
        if point_key in data:
            return _normalise_coordinate_container(data[point_key])

    if "polygon" in data:
        polygon_value = data["polygon"]
        if isinstance(polygon_value, dict):
            for point_key in ("coordinates", "points"):
                if point_key in polygon_value:
                    return _normalise_coordinate_container(polygon_value[point_key])
        return _normalise_coordinate_container(polygon_value)

    geometry = data.get("geometry")
    if isinstance(geometry, dict):
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "Polygon" and coordinates is not None:
            return _normalise_coordinate_container(coordinates)
        if geometry_type == "MultiPolygon" and coordinates:
            exterior_rings = [
                polygon[0]
                for polygon in coordinates
                if polygon and polygon[0]
            ]
            if exterior_rings:
                return _normalise_coordinate_container(
                    max(exterior_rings, key=len)
                )
            raise ValueError("Empty MultiPolygon.")

    if data.get("type") == "FeatureCollection":
        candidate_boundaries: list[list[list[float]]] = []
        for feature in data.get("features", []):
            try:
                candidate_boundaries.append(_extract_points(feature))
            except ValueError:
                continue
        if candidate_boundaries:
            return max(candidate_boundaries, key=len)
        raise ValueError("Empty FeatureCollection.")

    # Last-resort recursive search handles older nested wrapper objects.
    for nested_value in data.values():
        if not isinstance(nested_value, dict):
            continue
        try:
            return _extract_points(nested_value)
        except ValueError:
            continue

    raise ValueError(
        "Could not find polygon coordinates in field boundary JSON. "
        f"Top-level keys were: {list(data.keys())}"
    )


def _normalise_coordinate_container(coordinates: Any) -> list[list[float]]:
    """Peel Polygon/MultiPolygon wrappers until [[x, y], ...] remains."""
    coordinate_container = coordinates
    while (
        isinstance(coordinate_container, list)
        and coordinate_container
        and isinstance(coordinate_container[0], list)
        and coordinate_container[0]
        and isinstance(coordinate_container[0][0], list)
    ):
        coordinate_container = coordinate_container[0]

    if not isinstance(coordinate_container, list) or len(coordinate_container) < 3:
        raise ValueError("Boundary has fewer than three coordinate points.")

    points: list[list[float]] = []
    for coordinate_pair in coordinate_container:
        if not isinstance(coordinate_pair, (list, tuple)) or len(coordinate_pair) < 2:
            raise ValueError("Boundary point is not [x, y].")
        points.append([float(coordinate_pair[0]), float(coordinate_pair[1])])

    return _strip_closed_coordinate(points)


def _strip_closed_coordinate(coordinates) -> list[list[float]]:
    points = [[float(point[0]), float(point[1])] for point in coordinates]
    if len(points) >= 2 and _points_are_equal(points[0], points[-1]):
        points = points[:-1]

    if len(points) < 3:
        raise ValueError("A polygon needs at least three distinct points.")
    return points


def _points_are_equal(first_point: list[float], second_point: list[float]) -> bool:
    return (
        abs(first_point[0] - second_point[0]) < COORDINATE_EQUALITY_TOLERANCE
        and abs(first_point[1] - second_point[1]) < COORDINATE_EQUALITY_TOLERANCE
    )


def write_boundary_points_compatible(
    path: Path,
    points: list[list[float]],
    *,
    source: str = "approved",
) -> None:
    """Write canonical points plus legacy aliases used by older project versions."""
    cleaned_points = _strip_closed_coordinate(points)
    write_json(
        path,
        {
            "points": cleaned_points,
            "pixel_points": cleaned_points,
            "polygon_pixels": cleaned_points,
            "source": source,
        },
    )


# Compatibility alias for old local patches that imported this private name.
_unique_dir = unique_directory
