from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .gnss import wait_for_rtk_position
from .mapbox import ImageSettings, download_satellite_image
from .models import Position
from .review import review_capture
from .usage import print_mapbox_usage, record_mapbox_request


load_dotenv()


def _env_float(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number, got: {value!r}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire a GPS position (or use a mock position) and download a centred satellite image."
    )
    parser.add_argument(
        "--gps-mode",
        choices=("live", "mock"),
        default=os.getenv("GPS_MODE", "live").strip().lower() or "live",
        help="Use live GNSS hardware or a mock coordinate",
    )
    parser.add_argument(
        "--port",
        default=os.getenv("GPS_PORT", "/dev/serial0"),
        help="NMEA serial port",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=int(os.getenv("GPS_BAUD", "115200")),
        help="Serial baud rate",
    )
    parser.add_argument("--samples", type=int, default=5, help="Consecutive RTK samples")
    parser.add_argument("--timeout", type=float, default=300, help="GNSS timeout in seconds")
    parser.add_argument("--allow-float", action="store_true", help="Accept RTK float fixes")
    parser.add_argument("--zoom", type=float, default=18, help="Mapbox zoom level")
    parser.add_argument("--width", type=int, default=1000, help="Image width")
    parser.add_argument("--height", type=int, default=1000, help="Image height")
    parser.add_argument("--marker", action="store_true", help="Draw a pin at the coordinate")
    parser.add_argument(
        "--no-review",
        action="store_true",
        help="Save the image without opening the confirmation window",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    parser.add_argument(
        "--mock-lat",
        type=float,
        default=_env_float("MOCK_LAT"),
        help="Mock latitude",
    )
    parser.add_argument(
        "--mock-lon",
        type=float,
        default=_env_float("MOCK_LON"),
        help="Mock longitude",
    )
    return parser


def get_mapbox_token() -> str:
    token = os.getenv("MAPBOX_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError(
        "MAPBOX_TOKEN is not configured. Add your Mapbox token to the .env file."
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)

        if args.gps_mode == "mock":
            if args.mock_lat is None or args.mock_lon is None:
                raise RuntimeError(
                    "GPS_MODE is set to mock, but MOCK_LAT and MOCK_LON are not configured in .env."
                )

        token = get_mapbox_token()

        settings = ImageSettings(
            zoom=args.zoom,
            width=args.width,
            height=args.height,
            marker=args.marker,
        )

        if args.gps_mode == "mock":
            position = Position.mock(args.mock_lat, args.mock_lon)
            source = "mock"
            print(
                f"GPS bypass enabled: using mock position "
                f"{position.latitude:.8f}, {position.longitude:.8f}"
            )
        else:
            print(f"Reading NMEA from {args.port} at {args.baud} baud...")
            position = wait_for_rtk_position(
                port=args.port,
                baud=args.baud,
                samples_required=args.samples,
                timeout_s=args.timeout,
                allow_float=args.allow_float,
            )
            source = "nmea_gga"

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        capture_dir = args.output_dir / timestamp
        image_path = capture_dir / "satellite.png"
        metadata_path = capture_dir / "metadata.json"

        print(
            f"Position acquired: {position.latitude:.8f}, "
            f"{position.longitude:.8f}; downloading imagery..."
        )
        download_satellite_image(
            latitude=position.latitude,
            longitude=position.longitude,
            token=token,
            settings=settings,
            destination=image_path,
        )
        usage = record_mapbox_request()
        print_mapbox_usage(usage)

        metadata = {
            "position": position.as_dict(),
            "position_source": source,
            "image_provider": "Mapbox Satellite",
            "image_settings": settings.as_dict(),
            "image_file": image_path.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

        print(f"Saved image: {image_path}")
        print(f"Saved metadata: {metadata_path}")

        if not args.no_review:
            confirmed, area_name = review_capture(
                image_path=image_path,
                metadata_path=metadata_path,
                latitude=position.latitude,
                longitude=position.longitude,
            )
            if confirmed:
                print(f"Confirmed area: {area_name}")
            else:
                print("Image was not confirmed.")
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
