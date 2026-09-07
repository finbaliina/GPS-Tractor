from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
FARMS_DIR = ROOT / "farms"
CAPTURES_DIR = ROOT / "captures"


def ensure_storage() -> None:
    FARMS_DIR.mkdir(exist_ok=True)
    CAPTURES_DIR.mkdir(exist_ok=True)


def slugify(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip()).strip("_").lower()
    return value or "field"


def _unique_dir(parent: Path, slug: str) -> Path:
    candidate = parent / slug
    if not candidate.exists():
        return candidate

    i = 2
    while (parent / f"{slug}_{i}").exists():
        i += 1
    return parent / f"{slug}_{i}"


def list_farms() -> list[dict[str, Any]]:
    ensure_storage()
    farms = []

    for farm_dir in sorted(p for p in FARMS_DIR.iterdir() if p.is_dir()):
        metadata_path = farm_dir / "farm.json"
        if metadata_path.exists():
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        else:
            data = {"name": farm_dir.name}

        fields_dir = farm_dir / "fields"
        field_count = 0
        if fields_dir.exists():
            field_count = len([p for p in fields_dir.iterdir() if p.is_dir()])

        farms.append({
            "id": farm_dir.name,
            "name": data.get("name", farm_dir.name),
            "field_count": field_count,
            "setup_complete": bool(data.get("setup_complete", False)),
        })

    return farms


def create_farm(name: str) -> dict[str, str]:
    ensure_storage()

    name = name.strip()
    if not name:
        raise ValueError("Farm name cannot be empty.")

    farm_dir = _unique_dir(FARMS_DIR, slugify(name))
    (farm_dir / "fields").mkdir(parents=True)

    (farm_dir / "farm.json").write_text(
        json.dumps({"name": name}, indent=2) + "\n",
        encoding="utf-8",
    )

    return {"id": farm_dir.name, "name": name}


def get_farm(farm_id: str) -> dict[str, Any]:
    farm_dir = FARMS_DIR / farm_id
    if not farm_dir.exists():
        raise FileNotFoundError(farm_id)

    metadata_path = farm_dir / "farm.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    fields = []
    fields_dir = farm_dir / "fields"
    fields_dir.mkdir(exist_ok=True)

    for field_dir_path in sorted(p for p in fields_dir.iterdir() if p.is_dir()):
        field_json = field_dir_path / "field.json"

        if field_json.exists():
            data = json.loads(field_json.read_text(encoding="utf-8"))
        else:
            data = {"name": field_dir_path.name}

        fields.append({
            "id": field_dir_path.name,
            "name": data.get("name", field_dir_path.name),
            "route_ready": (field_dir_path / "route_plan.json").exists(),
            "has_terrain": (field_dir_path / "terrain_data.npz").exists(),
            "boundary_source": data.get("boundary_source", "unknown"),
        })

    return {
        "id": farm_id,
        "name": metadata.get("name", farm_id),
        "postcode": metadata.get("postcode"),
        "setup_complete": bool(metadata.get("setup_complete", False)),
        "cadastral_parcel_count": int(metadata.get("cadastral_parcel_count", 0)),
        "cadastral_sources": metadata.get("cadastral_sources", []),
        "fields": fields,
    }


def list_capture_candidates() -> list[dict[str, str]]:
    ensure_storage()
    result = []

    for capture in sorted(
        (p for p in CAPTURES_DIR.iterdir() if p.is_dir()),
        reverse=True,
    ):
        satellite = capture / "satellite.png"
        boundary = capture / "field_boundary.json"

        if satellite.exists() and boundary.exists():
            result.append({"id": capture.name, "label": capture.name})

    return result


def import_capture_as_field(
    farm_id: str,
    capture_id: str,
    field_name: str,
) -> dict[str, str]:
    farm_dir = FARMS_DIR / farm_id
    capture_dir = CAPTURES_DIR / capture_id

    if not farm_dir.exists():
        raise FileNotFoundError(f"Farm not found: {farm_id}")

    if not capture_dir.exists():
        raise FileNotFoundError(f"Capture not found: {capture_id}")

    if not (capture_dir / "satellite.png").exists():
        raise FileNotFoundError("Capture does not contain satellite.png")

    if not (capture_dir / "field_boundary.json").exists():
        raise FileNotFoundError("Capture does not contain field_boundary.json")

    field_name = field_name.strip()
    if not field_name:
        raise ValueError("Field name cannot be empty.")

    fields_dir = farm_dir / "fields"
    fields_dir.mkdir(exist_ok=True)

    field_dir_path = _unique_dir(fields_dir, slugify(field_name))
    field_dir_path.mkdir()

    copy_names = [
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
    ]

    for name in copy_names:
        source = capture_dir / name
        if source.exists():
            shutil.copy2(source, field_dir_path / name)

    shutil.copy2(
        capture_dir / "field_boundary.json",
        field_dir_path / "field_boundary_detected.json",
    )

    detected_points = read_boundary_points(
        field_dir_path / "field_boundary_detected.json"
    )

    (field_dir_path / "field_boundary_approved.json").write_text(
        json.dumps(
            {"source": "cv", "points": detected_points},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    (field_dir_path / "field.json").write_text(
        json.dumps(
            {
                "name": field_name,
                "source_capture": capture_id,
                "boundary_source": "cv",
                "boundary_locked": False,
                "route_needs_regeneration": False,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    return {"id": field_dir_path.name, "name": field_name}


def field_dir(farm_id: str, field_id: str) -> Path:
    path = FARMS_DIR / farm_id / "fields" / field_id
    if not path.exists():
        raise FileNotFoundError(field_id)
    return path


def get_field(farm_id: str, field_id: str) -> dict[str, Any]:
    path = field_dir(farm_id, field_id)

    field_json = path / "field.json"
    if field_json.exists():
        data = json.loads(field_json.read_text(encoding="utf-8"))
    else:
        data = {
            "name": field_id,
            "boundary_source": "cv",
            "boundary_locked": False,
        }

    detected_path = path / "field_boundary_detected.json"
    current_path = path / "field_boundary.json"
    approved_path = path / "field_boundary_approved.json"

    # Recover older fields automatically.
    # If field_boundary_detected.json wasn't created by the first UI version,
    # preserve the current boundary as the detected baseline.
    if not detected_path.exists():
        if not current_path.exists():
            raise FileNotFoundError(
                f"No field boundary found for {field_id}."
            )
        shutil.copy2(current_path, detected_path)

    detected = read_boundary_points(detected_path)

    # If the approved file is missing or was created in a format the UI cannot
    # read, rebuild it from the current/detected boundary rather than crashing.
    try:
        if approved_path.exists():
            approved_data = json.loads(approved_path.read_text(encoding="utf-8"))
            points = _extract_points(approved_data)
        elif current_path.exists():
            points = read_boundary_points(current_path)
        else:
            points = detected
    except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
        points = detected

    if not approved_path.exists():
        approved_path.write_text(
            json.dumps(
                {
                    "source": data.get("boundary_source", "cv"),
                    "points": points,
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

    return {
        "id": field_id,
        "name": data.get("name", field_id),
        "boundary_source": data.get("boundary_source", "cv"),
        "boundary_locked": bool(data.get("boundary_locked", False)),
        "points": points,
        "detected_points": detected,
        "route_ready": (path / "route_plan.json").exists(),
    }


def save_boundary(
    farm_id: str,
    field_id: str,
    points: list[list[float]],
    source: str = "manual_edit",
) -> None:
    if len(points) < 3:
        raise ValueError("A field boundary needs at least three points.")

    clean_points = _strip_closed_coordinate(points)
    path = field_dir(farm_id, field_id)

    (path / "field_boundary_approved.json").write_text(
        json.dumps(
            {
                "source": source,
                "points": clean_points,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    # This remains easy for the UI to read.
    write_boundary_points_compatible(
        path / "field_boundary.json",
        clean_points,
    )

    field_json = path / "field.json"
    if field_json.exists():
        data = json.loads(field_json.read_text(encoding="utf-8"))
    else:
        data = {"name": field_id}

    data["boundary_source"] = source
    data["boundary_locked"] = True
    data["route_needs_regeneration"] = True

    field_json.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )


def reset_boundary(farm_id: str, field_id: str) -> list[list[float]]:
    path = field_dir(farm_id, field_id)
    points = read_boundary_points(path / "field_boundary_detected.json")

    (path / "field_boundary_approved.json").write_text(
        json.dumps({"source": "cv", "points": points}, indent=2) + "\n",
        encoding="utf-8",
    )

    write_boundary_points_compatible(
        path / "field_boundary.json",
        points,
    )

    field_json = path / "field.json"
    if field_json.exists():
        data = json.loads(field_json.read_text(encoding="utf-8"))
    else:
        data = {"name": field_id}

    data["boundary_source"] = "cv"
    data["boundary_locked"] = False
    data["route_needs_regeneration"] = True

    field_json.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )

    return points


def read_boundary_points(path: Path) -> list[list[float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return _extract_points(data)


def _extract_points(data: Any) -> list[list[float]]:
    """
    Accept all boundary JSON formats used by the GPS Tractor project so far.

    Supported examples:
      {"points": [[x,y], ...]}
      {"polygon_pixels": [[x,y], ...]}
      {"boundary_pixels": [[x,y], ...]}
      {"coordinates": [[x,y], ...]}
      {"polygon": [[x,y], ...]}
      {"polygon": {"coordinates": [[[x,y], ...]]}}
      {"geometry": {"type":"Polygon", "coordinates":[[[x,y], ...]]}}
      GeoJSON Feature / FeatureCollection
      raw [[x,y], ...]
    """
    if isinstance(data, list):
        return _normalise_coordinate_container(data)

    if not isinstance(data, dict):
        raise ValueError("Boundary JSON is neither an object nor a list.")

    # Current UI format.
    for key in (
        "points",
        "pixel_points",
        "polygon_pixels",
        "boundary_pixels",
        "contour_points",
        "vertices",
        "coordinates",
    ):
        if key in data:
            return _normalise_coordinate_container(data[key])

    if "polygon" in data:
        polygon = data["polygon"]
        if isinstance(polygon, dict):
            if "coordinates" in polygon:
                return _normalise_coordinate_container(polygon["coordinates"])
            if "points" in polygon:
                return _normalise_coordinate_container(polygon["points"])
        return _normalise_coordinate_container(polygon)

    # GeoJSON geometry.
    if "geometry" in data and isinstance(data["geometry"], dict):
        geometry = data["geometry"]
        if geometry.get("type") == "Polygon" and "coordinates" in geometry:
            return _normalise_coordinate_container(geometry["coordinates"])
        if geometry.get("type") == "MultiPolygon" and "coordinates" in geometry:
            polygons = geometry["coordinates"]
            if not polygons:
                raise ValueError("Empty MultiPolygon.")
            # Use the largest ring by point count as a safe UI fallback.
            rings = []
            for poly in polygons:
                if poly:
                    rings.extend(poly[:1])
            if not rings:
                raise ValueError("Empty MultiPolygon.")
            return _normalise_coordinate_container(max(rings, key=len))

    # GeoJSON Feature.
    if data.get("type") == "Feature" and "geometry" in data:
        return _extract_points({"geometry": data["geometry"]})

    # GeoJSON FeatureCollection.
    if data.get("type") == "FeatureCollection":
        features = data.get("features", [])
        if not features:
            raise ValueError("Empty FeatureCollection.")
        candidates = []
        for feature in features:
            try:
                candidates.append(_extract_points(feature))
            except ValueError:
                pass
        if candidates:
            return max(candidates, key=len)

    # Last-resort recursive search through likely nested dicts.
    for key, value in data.items():
        if isinstance(value, dict):
            try:
                return _extract_points(value)
            except ValueError:
                pass

    raise ValueError(
        "Could not find polygon coordinates in field boundary JSON. "
        f"Top-level keys were: {list(data.keys())}"
    )


def _normalise_coordinate_container(coords: Any) -> list[list[float]]:
    """
    Peel nested Polygon coordinate containers until we reach [[x,y], ...].
    """
    value = coords

    while (
        isinstance(value, list)
        and value
        and isinstance(value[0], list)
        and value[0]
        and isinstance(value[0][0], list)
    ):
        # Polygon -> first exterior ring.
        # MultiPolygon -> keep drilling into first polygon/ring.
        value = value[0]

    if not isinstance(value, list) or len(value) < 3:
        raise ValueError("Boundary has fewer than three coordinate points.")

    points = []

    for p in value:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise ValueError("Boundary point is not [x, y].")

        points.append([float(p[0]), float(p[1])])

    return _strip_closed_coordinate(points)


def _strip_closed_coordinate(coords) -> list[list[float]]:
    points = [[float(p[0]), float(p[1])] for p in coords]

    if len(points) >= 2:
        if (
            abs(points[0][0] - points[-1][0]) < 1e-9
            and abs(points[0][1] - points[-1][1]) < 1e-9
        ):
            points = points[:-1]

    if len(points) < 3:
        raise ValueError("A polygon needs at least three distinct points.")

    return points


def write_boundary_points_compatible(path: Path, points: list[list[float]]) -> None:
    path.write_text(
        json.dumps(
            {
                "points": points,
                "pixel_points": points,
                "polygon_pixels": points,
                "source": "approved",
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
