from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gps_location import Position
from json_io import write_json
from mapbox import fetch_satellite_image, metres_per_pixel
from settings import settings


@dataclass(frozen=True)
class Capture:
    folder: Path
    image_path: Path
    metadata_path: Path
    metres_per_pixel: float


def capture_satellite_image(position: Position) -> Capture:
    """Download and save the normal single-position satellite capture."""
    map_settings = settings.mapbox
    capture_folder = Path("captures") / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    capture_folder.mkdir(parents=True, exist_ok=True)

    satellite_path = capture_folder / "satellite.png"
    metadata_path = capture_folder / "metadata.json"

    fetch_satellite_image(
        latitude_deg=position.latitude,
        longitude_deg=position.longitude,
        zoom=map_settings.capture_zoom,
        width_px=map_settings.capture_width_px,
        height_px=map_settings.capture_height_px,
        destination=satellite_path,
    )

    image_metres_per_pixel = metres_per_pixel(
        position.latitude,
        map_settings.capture_zoom,
    )
    write_json(
        metadata_path,
        {
            "position": position.as_dict(),
            "image_settings": {
                "zoom": map_settings.capture_zoom,
                "width": map_settings.capture_width_px,
                "height": map_settings.capture_height_px,
            },
            "metres_per_pixel": image_metres_per_pixel,
            "image_width_metres": map_settings.capture_width_px
            * image_metres_per_pixel,
            "image_height_metres": map_settings.capture_height_px
            * image_metres_per_pixel,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    print(f"Saved image: {satellite_path}")
    print(f"Scale: {image_metres_per_pixel:.3f} metres/pixel")

    return Capture(
        folder=capture_folder,
        image_path=satellite_path,
        metadata_path=metadata_path,
        metres_per_pixel=image_metres_per_pixel,
    )
