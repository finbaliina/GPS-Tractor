from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
import requests
from PIL import Image
from shapely.geometry import Polygon


@dataclass(frozen=True)
class TerrainData:
    elevation_grid: np.ndarray
    slope_grid_deg: np.ndarray
    field_mask: np.ndarray
    image_x: np.ndarray
    image_y: np.ndarray
    mean_slope_deg: float
    median_slope_deg: float
    p90_slope_deg: float
    max_slope_deg: float
    min_elevation_m: float
    max_elevation_m: float
    contour_interval_m: float
    terrain_json_path: Path
    overlay_path: Path
    data_path: Path


STYLE_TILE_SIZE = 512
TERRAIN_TILE_PIXELS = 512


def get_terrain(capture, field_polygon: Polygon) -> TerrainData:
    """Download Mapbox Terrain-RGB elevation data aligned to the satellite image."""
    token = os.environ["MAPBOX_TOKEN"]

    metadata = _load_metadata(capture.metadata_path)
    latitude, longitude = _position_from_metadata(metadata)
    map_zoom = float(metadata.get("zoom", os.getenv("MAP_ZOOM", "14")))
    image_width = int(metadata.get("width", _image_size(capture.image_path)[0]))
    image_height = int(metadata.get("height", _image_size(capture.image_path)[1]))

    terrain_zoom = int(os.getenv("TERRAIN_TILE_ZOOM", "14"))
    terrain_zoom = max(0, min(14, terrain_zoom))

    grid_long_side = max(50, int(os.getenv("TERRAIN_GRID_SIZE", "300")))
    contour_interval_m = max(0.1, float(os.getenv("CONTOUR_INTERVAL_M", "2.0")))
    timeout_s = float(os.getenv("TERRAIN_REQUEST_TIMEOUT", "30"))

    grid_w, grid_h = _grid_shape(image_width, image_height, grid_long_side)

    image_x = np.linspace(0.0, image_width - 1.0, grid_w)
    image_y = np.linspace(0.0, image_height - 1.0, grid_h)
    xx, yy = np.meshgrid(image_x, image_y)

    lon_grid, lat_grid = _image_pixels_to_lonlat(
        xx,
        yy,
        centre_lon=longitude,
        centre_lat=latitude,
        map_zoom=map_zoom,
        image_width=image_width,
        image_height=image_height,
    )

    elevation = _download_elevation_grid(
        lon_grid,
        lat_grid,
        terrain_zoom=terrain_zoom,
        token=token,
        timeout_s=timeout_s,
    )

    field_mask = _polygon_mask(
        field_polygon,
        grid_w=grid_w,
        grid_h=grid_h,
        image_width=image_width,
        image_height=image_height,
    )

    if not np.any(field_mask):
        raise RuntimeError("Field polygon does not overlap the terrain grid.")

    metres_per_pixel = float(capture.metres_per_pixel)
    dx_m = metres_per_pixel * (image_width - 1) / max(grid_w - 1, 1)
    dy_m = metres_per_pixel * (image_height - 1) / max(grid_h - 1, 1)

    dz_dy, dz_dx = np.gradient(elevation, dy_m, dx_m)
    slope_deg = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))

    field_slopes = slope_deg[field_mask]
    field_elev = elevation[field_mask]

    mean_slope = float(np.mean(field_slopes))
    median_slope = float(np.median(field_slopes))
    p90_slope = float(np.percentile(field_slopes, 90))
    max_slope = float(np.max(field_slopes))
    min_elev = float(np.min(field_elev))
    max_elev = float(np.max(field_elev))

    output_dir = Path(capture.folder)
    data_path = output_dir / "terrain_data.npz"
    terrain_json_path = output_dir / "terrain.json"
    overlay_path = output_dir / "terrain_overlay.png"

    np.savez_compressed(
        data_path,
        elevation_m=elevation.astype(np.float32),
        slope_deg=slope_deg.astype(np.float32),
        field_mask=field_mask.astype(np.uint8),
        image_x=image_x.astype(np.float32),
        image_y=image_y.astype(np.float32),
    )

    threshold = float(os.getenv("CONTOUR_SLOPE_THRESHOLD_DEG", "5.0"))
    terrain_summary = {
        "terrain_tile_zoom": terrain_zoom,
        "grid_width": grid_w,
        "grid_height": grid_h,
        "contour_interval_m": contour_interval_m,
        "mean_slope_deg": mean_slope,
        "median_slope_deg": median_slope,
        "p90_slope_deg": p90_slope,
        "max_slope_deg": max_slope,
        "min_elevation_m": min_elev,
        "max_elevation_m": max_elev,
        "elevation_range_m": max_elev - min_elev,
        "contour_slope_threshold_deg": threshold,
        "contour_mode_candidate": p90_slope >= threshold,
    }
    terrain_json_path.write_text(json.dumps(terrain_summary, indent=2), encoding="utf-8")

    _draw_terrain_overlay(
        image_path=capture.image_path,
        field_polygon=field_polygon,
        elevation=elevation,
        field_mask=field_mask,
        image_x=image_x,
        image_y=image_y,
        contour_interval_m=contour_interval_m,
        output_path=overlay_path,
    )

    print(
        "Terrain: "
        f"{min_elev:.1f}-{max_elev:.1f} m, "
        f"median slope {median_slope:.1f}°, "
        f"90th percentile {p90_slope:.1f}°"
    )
    print(f"Saved terrain overlay: {overlay_path}")

    return TerrainData(
        elevation_grid=elevation,
        slope_grid_deg=slope_deg,
        field_mask=field_mask,
        image_x=image_x,
        image_y=image_y,
        mean_slope_deg=mean_slope,
        median_slope_deg=median_slope,
        p90_slope_deg=p90_slope,
        max_slope_deg=max_slope,
        min_elevation_m=min_elev,
        max_elevation_m=max_elev,
        contour_interval_m=contour_interval_m,
        terrain_json_path=terrain_json_path,
        overlay_path=overlay_path,
        data_path=data_path,
    )


def _load_metadata(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _position_from_metadata(metadata: dict) -> tuple[float, float]:
    position = metadata.get("position", {})
    lat = position.get("latitude", metadata.get("latitude"))
    lon = position.get("longitude", metadata.get("longitude"))

    if lat is None or lon is None:
        raise RuntimeError("metadata.json does not contain the capture latitude/longitude.")

    return float(lat), float(lon)


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def _grid_shape(width: int, height: int, long_side: int) -> tuple[int, int]:
    if width >= height:
        return long_side, max(2, round(long_side * height / width))
    return max(2, round(long_side * width / height)), long_side


def _lon_to_world_x(lon_deg):
    return (np.asarray(lon_deg) + 180.0) / 360.0


def _lat_to_world_y(lat_deg):
    lat = np.clip(np.asarray(lat_deg, dtype=float), -85.05112878, 85.05112878)
    lat_rad = np.radians(lat)
    return (1.0 - np.arcsinh(np.tan(lat_rad)) / math.pi) / 2.0


def _world_x_to_lon(x: np.ndarray) -> np.ndarray:
    return x * 360.0 - 180.0


def _world_y_to_lat(y: np.ndarray) -> np.ndarray:
    n = math.pi * (1.0 - 2.0 * y)
    return np.degrees(np.arctan(np.sinh(n)))


def _image_pixels_to_lonlat(
    x: np.ndarray,
    y: np.ndarray,
    centre_lon: float,
    centre_lat: float,
    map_zoom: float,
    image_width: int,
    image_height: int,
):
    world_size = STYLE_TILE_SIZE * (2.0 ** map_zoom)

    centre_x = float(_lon_to_world_x(centre_lon)) * world_size
    centre_y = float(_lat_to_world_y(centre_lat)) * world_size

    global_x = centre_x + (x - (image_width - 1) / 2.0)
    global_y = centre_y + (y - (image_height - 1) / 2.0)

    return _world_x_to_lon(global_x / world_size), _world_y_to_lat(global_y / world_size)


def _download_elevation_grid(
    lon_grid: np.ndarray,
    lat_grid: np.ndarray,
    terrain_zoom: int,
    token: str,
    timeout_s: float,
) -> np.ndarray:
    n = 2 ** terrain_zoom

    tile_x_float = _lon_to_world_x(lon_grid) * n
    tile_y_float = _lat_to_world_y(lat_grid) * n

    tile_x = np.floor(tile_x_float).astype(int) % n
    tile_y = np.clip(np.floor(tile_y_float).astype(int), 0, n - 1)

    pixel_x = np.floor((tile_x_float - np.floor(tile_x_float)) * TERRAIN_TILE_PIXELS).astype(int)
    pixel_y = np.floor((tile_y_float - np.floor(tile_y_float)) * TERRAIN_TILE_PIXELS).astype(int)
    pixel_x = np.clip(pixel_x, 0, TERRAIN_TILE_PIXELS - 1)
    pixel_y = np.clip(pixel_y, 0, TERRAIN_TILE_PIXELS - 1)

    result = np.empty(lon_grid.shape, dtype=np.float32)
    cache = {}

    unique_tiles = sorted(set(zip(tile_x.ravel().tolist(), tile_y.ravel().tolist())))
    print(f"Terrain: downloading {len(unique_tiles)} Mapbox tile(s)...")

    for tx, ty in unique_tiles:
        cache[(tx, ty)] = _fetch_terrain_tile(
            terrain_zoom, tx, ty, token=token, timeout_s=timeout_s
        )

    for tx, ty in unique_tiles:
        mask = (tile_x == tx) & (tile_y == ty)
        tile = cache[(tx, ty)]
        result[mask] = tile[pixel_y[mask], pixel_x[mask]]

    return result


def _fetch_terrain_tile(z: int, x: int, y: int, token: str, timeout_s: float):
    url = (
        f"https://api.mapbox.com/v4/mapbox.terrain-rgb/"
        f"{z}/{x}/{y}@2x.pngraw?access_token={token}"
    )

    response = requests.get(url, timeout=timeout_s)
    response.raise_for_status()

    with Image.open(BytesIO(response.content)) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32)

    if rgb.shape[:2] != (TERRAIN_TILE_PIXELS, TERRAIN_TILE_PIXELS):
        raise RuntimeError(
            f"Unexpected Terrain-RGB tile size {rgb.shape[1]}x{rgb.shape[0]}."
        )

    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    return -10000.0 + (r * 256.0 * 256.0 + g * 256.0 + b) * 0.1


def _polygon_mask(
    polygon: Polygon,
    grid_w: int,
    grid_h: int,
    image_width: int,
    image_height: int,
):
    mask = np.zeros((grid_h, grid_w), dtype=np.uint8)
    polygons = [polygon] if polygon.geom_type == "Polygon" else list(polygon.geoms)

    sx = (grid_w - 1) / max(image_width - 1, 1)
    sy = (grid_h - 1) / max(image_height - 1, 1)

    for poly in polygons:
        exterior = np.asarray(poly.exterior.coords, dtype=float).copy()
        exterior[:, 0] *= sx
        exterior[:, 1] *= sy
        cv2.fillPoly(mask, [np.rint(exterior).astype(np.int32)], 1)

        for ring in poly.interiors:
            hole = np.asarray(ring.coords, dtype=float).copy()
            hole[:, 0] *= sx
            hole[:, 1] *= sy
            cv2.fillPoly(mask, [np.rint(hole).astype(np.int32)], 0)

    return mask.astype(bool)


def _draw_terrain_overlay(
    image_path: Path,
    field_polygon: Polygon,
    elevation: np.ndarray,
    field_mask: np.ndarray,
    image_x: np.ndarray,
    image_y: np.ndarray,
    contour_interval_m: float,
    output_path: Path,
):
    with Image.open(image_path) as source:
        satellite = np.asarray(source.convert("RGB"))

    field_elev = elevation[field_mask]
    min_elev = float(np.min(field_elev))
    max_elev = float(np.max(field_elev))

    first_level = math.ceil(min_elev / contour_interval_m) * contour_interval_m
    last_level = math.floor(max_elev / contour_interval_m) * contour_interval_m

    if last_level >= first_level:
        levels = np.arange(
            first_level,
            last_level + contour_interval_m * 0.5,
            contour_interval_m,
        )
    else:
        levels = np.array([], dtype=float)

    masked_elevation = np.ma.masked_where(~field_mask, elevation)

    height, width = satellite.shape[:2]
    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(satellite, extent=[0, width - 1, height - 1, 0])

    if len(levels) > 0:
        contours = ax.contour(
            image_x,
            image_y,
            masked_elevation,
            levels=levels,
            linewidths=1.2,
        )
        ax.clabel(contours, inline=True, fontsize=7, fmt="%.0f m")

    polygons = [field_polygon] if field_polygon.geom_type == "Polygon" else list(field_polygon.geoms)
    for poly in polygons:
        coords = np.asarray(poly.exterior.coords)
        ax.plot(coords[:, 0], coords[:, 1], linewidth=2)

    ax.set_xlim(0, width - 1)
    ax.set_ylim(height - 1, 0)
    ax.axis("off")
    fig.savefig(output_path, dpi=dpi, pad_inches=0)
    plt.close(fig)
