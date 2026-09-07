from __future__ import annotations

"""Small adapter around the GB cadastral parcel datasets.

The UI only needs candidate polygons around one coordinate during initial setup.
After the farmer confirms the candidates, the selected geometry is copied into
that farm's local folder and this module is no longer involved in normal use.

Supported local files under cadastral_data/:
  *.gpkg, *.shp, *.geojson, *.json, *.gml

Files can be arranged in subfolders.  The loader uses GeoPandas, so the source
CRS may be British National Grid (EPSG:27700) or any CRS GeoPandas understands.
"""

from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon, mapping
from shapely.ops import transform


ROOT = Path(__file__).resolve().parent
CADASTRAL_DIR = ROOT / "cadastral_data"
SUPPORTED_SUFFIXES = {".gpkg", ".shp", ".geojson", ".json", ".gml"}


@dataclass
class CandidateParcel:
    id: str
    source: str
    geometry_wgs84: Any
    area_m2: float
    distance_m: float
    contains_search_point: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "geometry": mapping(self.geometry_wgs84),
            "area_m2": self.area_m2,
            "distance_m": self.distance_m,
            "contains_search_point": self.contains_search_point,
        }


def data_status() -> dict[str, Any]:
    CADASTRAL_DIR.mkdir(exist_ok=True)
    files = [
        p for p in CADASTRAL_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    return {
        "directory": str(CADASTRAL_DIR),
        "files": [str(p.relative_to(CADASTRAL_DIR)) for p in sorted(files)],
        "ready": bool(files),
    }


def find_candidate_parcels(
    latitude: float,
    longitude: float,
    search_radius_m: float = 4500.0,
    max_candidates: int = 80,
) -> list[dict[str, Any]]:
    """Return cadastral polygons near the farm search point.

    This deliberately searches LOCAL cached registry data only.  There is no
    live Land Registry/RoS request here, so after setup normal tractor use has
    no registry dependency.
    """
    status = data_status()

    if not status["ready"]:
        if _env_bool("CADASTRAL_DEMO", False):
            return [p.as_dict() for p in _demo_candidates(latitude, longitude)]
        return []

    try:
        import geopandas as gpd
    except ImportError as exc:
        raise RuntimeError(
            "Cadastral files are present but GeoPandas is not installed. "
            "Run: python -m pip install geopandas pyogrio"
        ) from exc

    search_gdf = gpd.GeoDataFrame(
        {"_search": [1]},
        geometry=[Point(longitude, latitude)],
        crs="EPSG:4326",
    ).to_crs("EPSG:27700")

    search_point_bng = search_gdf.geometry.iloc[0]
    search_area_bng = search_point_bng.buffer(search_radius_m)

    results: list[CandidateParcel] = []
    seen = set()

    for file_path in _data_files():
        source = _source_name(file_path)
        try:
            frame = gpd.read_file(file_path, bbox=search_area_bng.bounds)
        except Exception:
            # Some drivers cannot apply an EPSG:27700 bbox before reading.
            frame = gpd.read_file(file_path)

        if frame.empty:
            continue
        if frame.crs is None:
            # The official GB datasets are commonly supplied in BNG.  Failing
            # loudly is safer than silently treating metre coordinates as lat/lon.
            raise RuntimeError(f"Cadastral file has no CRS: {file_path}")

        frame_bng = frame.to_crs("EPSG:27700")
        frame_bng = frame_bng[
            frame_bng.geometry.notna()
            & ~frame_bng.geometry.is_empty
            & frame_bng.geometry.intersects(search_area_bng)
        ].copy()
        if frame_bng.empty:
            continue

        frame_wgs = frame_bng.to_crs("EPSG:4326")

        for idx in frame_bng.index:
            geom_bng = frame_bng.at[idx, "geometry"]
            geom_wgs = frame_wgs.at[idx, "geometry"]

            if geom_bng.geom_type not in {"Polygon", "MultiPolygon"}:
                continue

            parcel_id = _parcel_id(frame_bng.loc[idx], file_path, idx)
            key = (source, parcel_id)
            if key in seen:
                continue
            seen.add(key)

            results.append(
                CandidateParcel(
                    id=parcel_id,
                    source=source,
                    geometry_wgs84=geom_wgs,
                    area_m2=float(geom_bng.area),
                    distance_m=float(geom_bng.distance(search_point_bng)),
                    contains_search_point=bool(geom_bng.covers(search_point_bng)),
                )
            )

    # Containing parcels first, then nearby larger rural parcels.
    results.sort(
        key=lambda p: (
            not p.contains_search_point,
            p.distance_m,
            -p.area_m2,
        )
    )
    return [p.as_dict() for p in results[:max_candidates]]


def _data_files() -> list[Path]:
    CADASTRAL_DIR.mkdir(exist_ok=True)
    return sorted(
        p for p in CADASTRAL_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def _source_name(path: Path) -> str:
    lower = str(path.relative_to(CADASTRAL_DIR)).lower()
    if "scot" in lower or "ros" in lower:
        return "Registers of Scotland"
    if "hmlr" in lower or "inspire" in lower or "england" in lower or "wales" in lower:
        return "HM Land Registry"
    return path.stem


def _parcel_id(row, path: Path, idx: Any) -> str:
    preferred = [
        "INSPIREID", "inspireid", "INSPIRE_ID", "inspire_id",
        "ID", "id", "OBJECTID", "objectid", "fid", "FID",
    ]
    for key in preferred:
        if key in row.index:
            value = row[key]
            if value is not None and str(value).strip():
                return str(value)
    return f"{path.stem}:{idx}"


def _demo_candidates(lat: float, lon: float) -> list[CandidateParcel]:
    """Synthetic polygons for UI testing only; never enabled automatically."""
    # Convert rough metres to degrees near the search point.
    dy = 1.0 / 111_320.0
    dx = 1.0 / (111_320.0 * max(math.cos(math.radians(lat)), 0.2))

    specs = [
        (-700, -450, 200, 280),
        (240, -430, 750, 300),
        (-650, 330, 80, 930),
        (140, 360, 850, 950),
        (900, -300, 1450, 420),
        (-1450, -500, -800, 420),
    ]
    out = []
    point = Point(lon, lat)

    for i, (xmin, ymin, xmax, ymax) in enumerate(specs, start=1):
        poly = Polygon([
            (lon + xmin * dx, lat + ymin * dy),
            (lon + xmax * dx, lat + ymin * dy),
            (lon + xmax * dx, lat + ymax * dy),
            (lon + xmin * dx, lat + ymax * dy),
        ])
        area = float((xmax - xmin) * (ymax - ymin))
        # Approximate distance is enough for demo ordering.
        cx = (xmin + xmax) / 2
        cy = (ymin + ymax) / 2
        dist = max(0.0, math.hypot(cx, cy) - math.sqrt(area) / 2)
        out.append(CandidateParcel(
            id=f"DEMO-{i:03d}",
            source="Demo cadastral data",
            geometry_wgs84=poly,
            area_m2=area,
            distance_m=dist,
            contains_search_point=poly.covers(point),
        ))
    return out


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
