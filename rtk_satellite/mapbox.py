from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

@dataclass(frozen=True)
class ImageSettings:
    zoom: float = 18
    width: int = 1000
    height: int = 1000
    marker: bool = False

    def validate(self) -> None:
        if not 0 <= self.zoom <= 22:
            raise ValueError("zoom must be between 0 and 22")
        if not 1 <= self.width <= 1280 or not 1 <= self.height <= 1280:
            raise ValueError("width and height must each be between 1 and 1280")

    def as_dict(self) -> dict[str, float | int | bool]:
        return {
            "zoom": self.zoom,
            "width": self.width,
            "height": self.height,
            "marker": self.marker,
        }


def build_static_image_url(
    latitude: float,
    longitude: float,
    token: str,
    settings: ImageSettings,
) -> str:
    settings.validate()
    if not token:
        raise ValueError("A Mapbox access token is required")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Invalid latitude or longitude")

    centre = f"{longitude:.8f},{latitude:.8f},{settings.zoom:g},0"
    overlay = ""
    if settings.marker:
        overlay = f"pin-s+e63946({longitude:.8f},{latitude:.8f})/"

    return (
        "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/"
        f"{overlay}{centre}/{settings.width}x{settings.height}"
        f"?access_token={quote(token, safe='')}"
    )


def download_satellite_image(
    latitude: float,
    longitude: float,
    token: str,
    settings: ImageSettings,
    destination: Path,
) -> None:
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError as error:
        raise RuntimeError(
            "requests is not installed; run: python -m pip install -r requirements.txt"
        ) from error

    url = build_static_image_url(latitude, longitude, token, settings)
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))

    response = session.get(url, timeout=(10, 45))
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if not content_type.startswith("image/"):
        raise RuntimeError(f"Mapbox returned unexpected content type: {content_type!r}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(response.content)
    temporary.replace(destination)
