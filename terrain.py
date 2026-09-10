from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import math

import cv2
import matplotlib

# Flask can generate terrain overlays from worker threads. Force Matplotlib to use
# a non-interactive backend so it never creates Tkinter GUI objects.
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from PIL import Image
from shapely.geometry import Polygon

from json_io import read_json, write_json
from mapbox import (
    MAPBOX_STATIC_TILE_SIZE_PX,
    WEB_MERCATOR_MAX_LATITUDE_DEG,
    require_mapbox_token,
)
from settings import settings


# Mapbox Terrain-RGB specification constants, not tuning parameters.
MAPBOX_TERRAIN_MAX_ZOOM = 14
TERRAIN_TILE_PIXELS = 512
RGB_CHANNEL_BASE = 256.0
TERRAIN_RGB_ELEVATION_OFFSET_M = -10_000.0
TERRAIN_RGB_ELEVATION_SCALE_M = 0.1
ROUTING_SLOPE_PERCENTILE = 90.0
MINIMUM_GRID_DIMENSION = 2
CONTOUR_LABEL_FORMAT = "%.0f m"


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


def get_terrain(capture, field_polygon: Polygon) -> TerrainData:
    """Download Mapbox Terrain-RGB data aligned to a saved satellite image."""
    terrain_settings = settings.terrain
    if terrain_settings.terrain_tile_zoom > MAPBOX_TERRAIN_MAX_ZOOM:
        raise ValueError(
            f"TERRAIN_TILE_ZOOM cannot exceed {MAPBOX_TERRAIN_MAX_ZOOM} "
            "for Mapbox Terrain-RGB."
        )
    if terrain_settings.grid_long_side_px < MINIMUM_GRID_DIMENSION:
        raise ValueError("TERRAIN_GRID_SIZE must be at least 2.")

    mapbox_token = require_mapbox_token()
    capture_metadata = read_json(capture.metadata_path)
    latitude_deg, longitude_deg = _position_from_metadata(capture_metadata)
    image_width_px, image_height_px = _image_dimensions(
        capture_metadata,
        capture.image_path,
    )
    map_zoom = _map_zoom(capture_metadata)

    terrain_grid_width, terrain_grid_height = _grid_shape(
        image_width_px,
        image_height_px,
        terrain_settings.grid_long_side_px,
    )
    image_x_coordinates = np.linspace(
        0.0,
        image_width_px - 1.0,
        terrain_grid_width,
    )
    image_y_coordinates = np.linspace(
        0.0,
        image_height_px - 1.0,
        terrain_grid_height,
    )
    image_x_grid, image_y_grid = np.meshgrid(
        image_x_coordinates,
        image_y_coordinates,
    )

    longitude_grid, latitude_grid = _image_pixels_to_lonlat(
        image_x_grid,
        image_y_grid,
        centre_longitude_deg=longitude_deg,
        centre_latitude_deg=latitude_deg,
        map_zoom=map_zoom,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
    )
    elevation_grid = _download_elevation_grid(
        longitude_grid,
        latitude_grid,
        terrain_zoom=terrain_settings.terrain_tile_zoom,
        mapbox_token=mapbox_token,
        request_timeout_s=terrain_settings.request_timeout_s,
    )

    field_mask = _polygon_mask(
        field_polygon,
        grid_width=terrain_grid_width,
        grid_height=terrain_grid_height,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
    )
    if not np.any(field_mask):
        raise RuntimeError("Field polygon does not overlap the terrain grid.")

    image_metres_per_pixel = float(capture.metres_per_pixel)
    terrain_step_x_m = (
        image_metres_per_pixel
        * (image_width_px - 1)
        / max(terrain_grid_width - 1, 1)
    )
    terrain_step_y_m = (
        image_metres_per_pixel
        * (image_height_px - 1)
        / max(terrain_grid_height - 1, 1)
    )
    elevation_gradient_y, elevation_gradient_x = np.gradient(
        elevation_grid,
        terrain_step_y_m,
        terrain_step_x_m,
    )
    slope_grid_deg = np.degrees(
        np.arctan(np.hypot(elevation_gradient_x, elevation_gradient_y))
    )

    field_slopes_deg = slope_grid_deg[field_mask]
    field_elevations_m = elevation_grid[field_mask]
    mean_slope_deg = float(np.mean(field_slopes_deg))
    median_slope_deg = float(np.median(field_slopes_deg))
    p90_slope_deg = float(
        np.percentile(field_slopes_deg, ROUTING_SLOPE_PERCENTILE)
    )
    maximum_slope_deg = float(np.max(field_slopes_deg))
    minimum_elevation_m = float(np.min(field_elevations_m))
    maximum_elevation_m = float(np.max(field_elevations_m))

    output_directory = Path(capture.folder)
    terrain_data_path = output_directory / "terrain_data.npz"
    terrain_json_path = output_directory / "terrain.json"
    terrain_overlay_path = output_directory / "terrain_overlay.png"

    np.savez_compressed(
        terrain_data_path,
        elevation_m=elevation_grid.astype(np.float32),
        slope_deg=slope_grid_deg.astype(np.float32),
        field_mask=field_mask.astype(np.uint8),
        image_x=image_x_coordinates.astype(np.float32),
        image_y=image_y_coordinates.astype(np.float32),
    )

    write_json(
        terrain_json_path,
        {
            "terrain_tile_zoom": terrain_settings.terrain_tile_zoom,
            "grid_width": terrain_grid_width,
            "grid_height": terrain_grid_height,
            "contour_interval_m": terrain_settings.contour_interval_m,
            "mean_slope_deg": mean_slope_deg,
            "median_slope_deg": median_slope_deg,
            "p90_slope_deg": p90_slope_deg,
            "max_slope_deg": maximum_slope_deg,
            "min_elevation_m": minimum_elevation_m,
            "max_elevation_m": maximum_elevation_m,
            "elevation_range_m": maximum_elevation_m - minimum_elevation_m,
            "contour_slope_threshold_deg": (
                terrain_settings.contour_slope_threshold_deg
            ),
            "contour_mode_candidate": (
                p90_slope_deg >= terrain_settings.contour_slope_threshold_deg
            ),
        },
    )

    _draw_terrain_overlay(
        image_path=capture.image_path,
        field_polygon=field_polygon,
        elevation_grid=elevation_grid,
        field_mask=field_mask,
        image_x_coordinates=image_x_coordinates,
        image_y_coordinates=image_y_coordinates,
        contour_interval_m=terrain_settings.contour_interval_m,
        output_path=terrain_overlay_path,
    )

    print(
        "Terrain: "
        f"{minimum_elevation_m:.1f}-{maximum_elevation_m:.1f} m, "
        f"median slope {median_slope_deg:.1f}°, "
        f"90th percentile {p90_slope_deg:.1f}°"
    )
    print(f"Saved terrain overlay: {terrain_overlay_path}")

    return TerrainData(
        elevation_grid=elevation_grid,
        slope_grid_deg=slope_grid_deg,
        field_mask=field_mask,
        image_x=image_x_coordinates,
        image_y=image_y_coordinates,
        mean_slope_deg=mean_slope_deg,
        median_slope_deg=median_slope_deg,
        p90_slope_deg=p90_slope_deg,
        max_slope_deg=maximum_slope_deg,
        min_elevation_m=minimum_elevation_m,
        max_elevation_m=maximum_elevation_m,
        contour_interval_m=terrain_settings.contour_interval_m,
        terrain_json_path=terrain_json_path,
        overlay_path=terrain_overlay_path,
        data_path=terrain_data_path,
    )


def load_saved_terrain(output_directory: Path) -> TerrainData:
    """Load terrain files previously created by get_terrain()."""
    terrain_data_path = output_directory / "terrain_data.npz"
    terrain_json_path = output_directory / "terrain.json"
    terrain_overlay_path = output_directory / "terrain_overlay.png"

    if not terrain_data_path.exists() or not terrain_json_path.exists():
        raise FileNotFoundError("Saved terrain data is incomplete.")

    saved_arrays = np.load(terrain_data_path)
    summary = read_json(terrain_json_path)
    return TerrainData(
        elevation_grid=np.asarray(saved_arrays["elevation_m"], dtype=np.float32),
        slope_grid_deg=np.asarray(saved_arrays["slope_deg"], dtype=np.float32),
        field_mask=np.asarray(saved_arrays["field_mask"], dtype=bool),
        image_x=np.asarray(saved_arrays["image_x"], dtype=float),
        image_y=np.asarray(saved_arrays["image_y"], dtype=float),
        mean_slope_deg=float(summary.get("mean_slope_deg", 0.0)),
        median_slope_deg=float(summary.get("median_slope_deg", 0.0)),
        p90_slope_deg=float(summary.get("p90_slope_deg", 0.0)),
        max_slope_deg=float(summary.get("max_slope_deg", 0.0)),
        min_elevation_m=float(summary.get("min_elevation_m", 0.0)),
        max_elevation_m=float(summary.get("max_elevation_m", 0.0)),
        contour_interval_m=float(
            summary.get("contour_interval_m", settings.terrain.contour_interval_m)
        ),
        terrain_json_path=terrain_json_path,
        overlay_path=terrain_overlay_path,
        data_path=terrain_data_path,
    )


def _position_from_metadata(metadata: dict) -> tuple[float, float]:
    position = metadata.get("position", {})
    latitude = position.get("latitude", metadata.get("latitude"))
    longitude = position.get("longitude", metadata.get("longitude"))
    if latitude is None or longitude is None:
        raise RuntimeError("metadata.json does not contain capture latitude/longitude.")
    return float(latitude), float(longitude)


def _map_zoom(metadata: dict) -> float:
    image_settings = metadata.get("image_settings", {})
    return float(
        metadata.get(
            "zoom",
            image_settings.get("zoom", settings.mapbox.capture_zoom),
        )
    )


def _image_dimensions(metadata: dict, image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as image:
        actual_width_px, actual_height_px = image.size

    image_settings = metadata.get("image_settings", {})
    width_px = int(metadata.get("width", image_settings.get("width", actual_width_px)))
    height_px = int(
        metadata.get("height", image_settings.get("height", actual_height_px))
    )
    return width_px, height_px


def _grid_shape(
    image_width_px: int,
    image_height_px: int,
    long_side_px: int,
) -> tuple[int, int]:
    if image_width_px >= image_height_px:
        grid_width = long_side_px
        grid_height = max(
            MINIMUM_GRID_DIMENSION,
            round(long_side_px * image_height_px / image_width_px),
        )
    else:
        grid_width = max(
            MINIMUM_GRID_DIMENSION,
            round(long_side_px * image_width_px / image_height_px),
        )
        grid_height = long_side_px
    return grid_width, grid_height


def _longitude_to_world_x(longitude_deg):
    return (np.asarray(longitude_deg) + 180.0) / 360.0


def _latitude_to_world_y(latitude_deg):
    clamped_latitude = np.clip(
        np.asarray(latitude_deg, dtype=float),
        -WEB_MERCATOR_MAX_LATITUDE_DEG,
        WEB_MERCATOR_MAX_LATITUDE_DEG,
    )
    latitude_rad = np.radians(clamped_latitude)
    return (1.0 - np.arcsinh(np.tan(latitude_rad)) / math.pi) / 2.0


def _world_x_to_longitude(world_x: np.ndarray) -> np.ndarray:
    return world_x * 360.0 - 180.0


def _world_y_to_latitude(world_y: np.ndarray) -> np.ndarray:
    mercator_value = math.pi * (1.0 - 2.0 * world_y)
    return np.degrees(np.arctan(np.sinh(mercator_value)))


def _image_pixels_to_lonlat(
    image_x: np.ndarray,
    image_y: np.ndarray,
    centre_longitude_deg: float,
    centre_latitude_deg: float,
    map_zoom: float,
    image_width_px: int,
    image_height_px: int,
):
    world_size_px = MAPBOX_STATIC_TILE_SIZE_PX * 2.0**map_zoom
    centre_world_x_px = float(_longitude_to_world_x(centre_longitude_deg)) * world_size_px
    centre_world_y_px = float(_latitude_to_world_y(centre_latitude_deg)) * world_size_px

    global_x_px = centre_world_x_px + image_x - (image_width_px - 1) / 2.0
    global_y_px = centre_world_y_px + image_y - (image_height_px - 1) / 2.0
    return (
        _world_x_to_longitude(global_x_px / world_size_px),
        _world_y_to_latitude(global_y_px / world_size_px),
    )


def _download_elevation_grid(
    longitude_grid: np.ndarray,
    latitude_grid: np.ndarray,
    terrain_zoom: int,
    mapbox_token: str,
    request_timeout_s: float,
) -> np.ndarray:
    tile_count_per_axis = 2**terrain_zoom
    tile_x_float = _longitude_to_world_x(longitude_grid) * tile_count_per_axis
    tile_y_float = _latitude_to_world_y(latitude_grid) * tile_count_per_axis

    tile_x_index = np.floor(tile_x_float).astype(int) % tile_count_per_axis
    tile_y_index = np.clip(
        np.floor(tile_y_float).astype(int),
        0,
        tile_count_per_axis - 1,
    )
    pixel_x = np.floor(
        (tile_x_float - np.floor(tile_x_float)) * TERRAIN_TILE_PIXELS
    ).astype(int)
    pixel_y = np.floor(
        (tile_y_float - np.floor(tile_y_float)) * TERRAIN_TILE_PIXELS
    ).astype(int)
    pixel_x = np.clip(pixel_x, 0, TERRAIN_TILE_PIXELS - 1)
    pixel_y = np.clip(pixel_y, 0, TERRAIN_TILE_PIXELS - 1)

    elevation_result = np.empty(longitude_grid.shape, dtype=np.float32)
    unique_tiles = sorted(
        set(zip(tile_x_index.ravel().tolist(), tile_y_index.ravel().tolist()))
    )
    print(f"Terrain: downloading {len(unique_tiles)} Mapbox tile(s)...")

    tile_cache = {
        (tile_x, tile_y): _fetch_terrain_tile(
            terrain_zoom,
            tile_x,
            tile_y,
            mapbox_token=mapbox_token,
            request_timeout_s=request_timeout_s,
        )
        for tile_x, tile_y in unique_tiles
    }

    for tile_x, tile_y in unique_tiles:
        grid_mask = (tile_x_index == tile_x) & (tile_y_index == tile_y)
        terrain_tile = tile_cache[(tile_x, tile_y)]
        elevation_result[grid_mask] = terrain_tile[
            pixel_y[grid_mask],
            pixel_x[grid_mask],
        ]

    return elevation_result


def _fetch_terrain_tile(
    zoom: int,
    tile_x: int,
    tile_y: int,
    mapbox_token: str,
    request_timeout_s: float,
) -> np.ndarray:
    request_url = (
        "https://api.mapbox.com/v4/mapbox.terrain-rgb/"
        f"{zoom}/{tile_x}/{tile_y}@2x.pngraw"
    )
    response = requests.get(
        request_url,
        params={"access_token": mapbox_token},
        timeout=request_timeout_s,
    )
    response.raise_for_status()

    with Image.open(BytesIO(response.content)) as image:
        rgb_values = np.asarray(image.convert("RGB"), dtype=np.float32)

    if rgb_values.shape[:2] != (TERRAIN_TILE_PIXELS, TERRAIN_TILE_PIXELS):
        raise RuntimeError(
            "Unexpected Terrain-RGB tile size "
            f"{rgb_values.shape[1]}x{rgb_values.shape[0]}."
        )

    red = rgb_values[:, :, 0]
    green = rgb_values[:, :, 1]
    blue = rgb_values[:, :, 2]
    encoded_value = (
        red * RGB_CHANNEL_BASE * RGB_CHANNEL_BASE
        + green * RGB_CHANNEL_BASE
        + blue
    )
    return (
        TERRAIN_RGB_ELEVATION_OFFSET_M
        + encoded_value * TERRAIN_RGB_ELEVATION_SCALE_M
    )


def _polygon_mask(
    polygon: Polygon,
    grid_width: int,
    grid_height: int,
    image_width_px: int,
    image_height_px: int,
) -> np.ndarray:
    mask = np.zeros((grid_height, grid_width), dtype=np.uint8)
    polygons = [polygon] if polygon.geom_type == "Polygon" else list(polygon.geoms)
    x_scale = (grid_width - 1) / max(image_width_px - 1, 1)
    y_scale = (grid_height - 1) / max(image_height_px - 1, 1)

    for polygon_part in polygons:
        exterior_points = np.asarray(
            polygon_part.exterior.coords,
            dtype=float,
        ).copy()
        exterior_points[:, 0] *= x_scale
        exterior_points[:, 1] *= y_scale
        cv2.fillPoly(mask, [np.rint(exterior_points).astype(np.int32)], 1)

        for interior_ring in polygon_part.interiors:
            hole_points = np.asarray(interior_ring.coords, dtype=float).copy()
            hole_points[:, 0] *= x_scale
            hole_points[:, 1] *= y_scale
            cv2.fillPoly(mask, [np.rint(hole_points).astype(np.int32)], 0)

    return mask.astype(bool)


def _draw_terrain_overlay(
    image_path: Path,
    field_polygon: Polygon,
    elevation_grid: np.ndarray,
    field_mask: np.ndarray,
    image_x_coordinates: np.ndarray,
    image_y_coordinates: np.ndarray,
    contour_interval_m: float,
    output_path: Path,
) -> None:
    overlay_settings = settings.terrain
    with Image.open(image_path) as source_image:
        satellite_image = np.asarray(source_image.convert("RGB"))

    field_elevations_m = elevation_grid[field_mask]
    minimum_elevation_m = float(np.min(field_elevations_m))
    maximum_elevation_m = float(np.max(field_elevations_m))
    first_contour_level_m = (
        math.ceil(minimum_elevation_m / contour_interval_m) * contour_interval_m
    )
    last_contour_level_m = (
        math.floor(maximum_elevation_m / contour_interval_m) * contour_interval_m
    )

    if last_contour_level_m >= first_contour_level_m:
        contour_levels_m = np.arange(
            first_contour_level_m,
            last_contour_level_m + contour_interval_m * 0.5,
            contour_interval_m,
        )
    else:
        contour_levels_m = np.array([], dtype=float)

    masked_elevation = np.ma.masked_where(~field_mask, elevation_grid)
    image_height_px, image_width_px = satellite_image.shape[:2]

    figure = plt.figure(
        figsize=(
            image_width_px / overlay_settings.overlay_dpi,
            image_height_px / overlay_settings.overlay_dpi,
        ),
        dpi=overlay_settings.overlay_dpi,
    )
    axes = figure.add_axes([0, 0, 1, 1])
    axes.imshow(
        satellite_image,
        extent=[0, image_width_px - 1, image_height_px - 1, 0],
    )

    if len(contour_levels_m) > 0:
        contours = axes.contour(
            image_x_coordinates,
            image_y_coordinates,
            masked_elevation,
            levels=contour_levels_m,
            linewidths=overlay_settings.overlay_contour_line_width,
        )
        axes.clabel(
            contours,
            inline=True,
            fontsize=overlay_settings.overlay_label_font_size,
            fmt=CONTOUR_LABEL_FORMAT,
        )

    polygon_parts = (
        [field_polygon]
        if field_polygon.geom_type == "Polygon"
        else list(field_polygon.geoms)
    )
    for polygon_part in polygon_parts:
        boundary_coordinates = np.asarray(polygon_part.exterior.coords)
        axes.plot(
            boundary_coordinates[:, 0],
            boundary_coordinates[:, 1],
            linewidth=overlay_settings.overlay_boundary_line_width,
        )

    axes.set_xlim(0, image_width_px - 1)
    axes.set_ylim(image_height_px - 1, 0)
    axes.axis("off")
    figure.savefig(
        output_path,
        dpi=overlay_settings.overlay_dpi,
        pad_inches=0,
    )
    plt.close(figure)
