from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .gnss import wait_for_rtk_position
from .mapbox import ImageSettings, download_satellite_image
from .models import Position


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Wait for an RTK position and download a centred satellite image."
    )
    parser.add_argument("--port", default="/dev/serial0", help="NMEA serial port")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--samples", type=int, default=5, help="Consecutive RTK samples")
    parser.add_argument("--timeout", type=float, default=300, help="GNSS timeout in seconds")
    parser.add_argument("--allow-float", action="store_true", help="Accept RTK float fixes")
    parser.add_argument("--zoom", type=float, default=18, help="Mapbox zoom level")
    parser.add_argument("--width", type=int, default=1000, help="Image width")
    parser.add_argument("--height", type=int, default=1000, help="Image height")
    parser.add_argument("--marker", action="store_true", help="Draw a pin at the coordinate")
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    parser.add_argument("--mock-lat", type=float, help="Test latitude without GNSS hardware")
    parser.add_argument("--mock-lon", type=float, help="Test longitude without GNSS hardware")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if (args.mock_lat is None) != (args.mock_lon is None):
        print("Error: --mock-lat and --mock-lon must be supplied together", file=sys.stderr)
        return 2

    token = os.environ.get("MAPBOX_TOKEN", "")
    if not token:
        print("Error: set the MAPBOX_TOKEN environment variable", file=sys.stderr)
        return 2

    settings = ImageSettings(
        zoom=args.zoom,
        width=args.width,
        height=args.height,
        marker=args.marker,
    )

    try:
        if args.mock_lat is not None:
            position = Position.mock(args.mock_lat, args.mock_lon)
            source = "mock"
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
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

