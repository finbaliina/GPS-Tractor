from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
from urllib.parse import quote

import requests

from gps_location import Position


EARTH_CIRCUMFERENCE_M = 40075016.68557849
MAPBOX_TILE_SIZE = 512


@dataclass(frozen=True)
class Capture:
    folder: Path
    image_path: Path
    metadata_path: Path
    metres_per_pixel: float


def capture_satellite_image(position: Position) -> Capture:
    token = os.getenv("MAPBOX_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MAPBOX_TOKEN is missing from .env")

    zoom = float(os.getenv("MAP_ZOOM", "14"))
    width = int(os.getenv("IMAGE_WIDTH", "1000"))
    height = int(os.getenv("IMAGE_HEIGHT", "1000"))

    folder = Path("captures") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    image_path = folder / "satellite.png"
    metadata_path = folder / "metadata.json"
    folder.mkdir(parents=True, exist_ok=True)

    url = _mapbox_url(position.latitude, position.longitude, zoom, width, height, token)
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    image_path.write_bytes(response.content)

    mpp = metres_per_pixel(position.latitude, zoom)

    metadata = {
        "position": position.as_dict(),
        "image_settings": {"zoom": zoom, "width": width, "height": height},
        "metres_per_pixel": mpp,
        "image_width_metres": width * mpp,
        "image_height_metres": height * mpp,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    print(f"Saved image: {image_path}")
    print(f"Scale: {mpp:.3f} metres/pixel")

    return Capture(folder, image_path, metadata_path, mpp)


def metres_per_pixel(latitude: float, zoom: float) -> float:
    return (
        EARTH_CIRCUMFERENCE_M
        * math.cos(math.radians(latitude))
        / (MAPBOX_TILE_SIZE * 2**zoom)
    )


def _mapbox_url(lat, lon, zoom, width, height, token):
    centre = f"{lon:.8f},{lat:.8f},{zoom:g},0"
    return (
        "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/"
        f"{centre}/{width}x{height}?access_token={quote(token, safe='')}"
    )
