from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .models import Position


@dataclass(frozen=True)
class GgaReading:
    latitude: float
    longitude: float
    altitude_m: float | None
    fix_quality: int
    satellites: int | None
    hdop: float | None


def parse_gga(line: str) -> GgaReading | None:
    """Parse one NMEA line, returning None for non-GGA or unusable messages."""
    try:
        content = _validated_nmea_content(line)
        fields = content.split(",")
        if len(fields) < 10 or fields[0][-3:] != "GGA":
            return None

        latitude = _nmea_coordinate(fields[2], fields[3])
        longitude = _nmea_coordinate(fields[4], fields[5])
        quality = int(fields[6] or 0)
    except (UnicodeError, ValueError, IndexError):
        return None

    if latitude == 0.0 and longitude == 0.0:
        return None

    return GgaReading(
        latitude=latitude,
        longitude=longitude,
        altitude_m=_optional_float(fields[9]),
        fix_quality=quality,
        satellites=_optional_int(fields[7]),
        hdop=_optional_float(fields[8]),
    )


def _validated_nmea_content(line: str) -> str:
    sentence = line.strip()
    if not sentence.startswith("$"):
        raise ValueError("NMEA sentence must start with $")

    payload = sentence[1:]
    if "*" not in payload:
        return payload

    content, checksum_text = payload.rsplit("*", 1)
    if len(checksum_text) != 2:
        raise ValueError("Invalid NMEA checksum")

    checksum = 0
    for character in content:
        checksum ^= ord(character)
    if checksum != int(checksum_text, 16):
        raise ValueError("NMEA checksum mismatch")
    return content


def _nmea_coordinate(value: str, hemisphere: str) -> float:
    if not value or hemisphere not in {"N", "S", "E", "W"}:
        raise ValueError("Invalid NMEA coordinate")

    degree_digits = 2 if hemisphere in {"N", "S"} else 3
    degrees = float(value[:degree_digits])
    minutes = float(value[degree_digits:])
    coordinate = degrees + minutes / 60
    if hemisphere in {"S", "W"}:
        coordinate = -coordinate
    return coordinate


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def combine_readings(readings: Iterable[GgaReading]) -> Position:
    values = list(readings)
    if not values:
        raise ValueError("At least one GGA reading is required")

    altitudes = [value.altitude_m for value in values if value.altitude_m is not None]
    hdops = [value.hdop for value in values if value.hdop is not None]
    satellites = [value.satellites for value in values if value.satellites is not None]

    return Position(
        latitude=statistics.median(value.latitude for value in values),
        longitude=statistics.median(value.longitude for value in values),
        altitude_m=statistics.median(altitudes) if altitudes else None,
        fix_quality=min(value.fix_quality for value in values),
        satellites=min(satellites) if satellites else None,
        hdop=statistics.median(hdops) if hdops else None,
        samples=len(values),
        acquired_at=datetime.now(timezone.utc).isoformat(),
    )


def wait_for_rtk_position(
    port: str,
    baud: int = 115200,
    samples_required: int = 5,
    timeout_s: float = 300,
    allow_float: bool = False,
) -> Position:
    """Wait for consecutive RTK GGA readings and return their median position."""
    try:
        import serial
    except ImportError as error:
        raise RuntimeError(
            "pyserial is not installed; run: python -m pip install -r requirements.txt"
        ) from error

    if samples_required < 1:
        raise ValueError("samples_required must be at least 1")

    accepted_qualities = {4, 5} if allow_float else {4}
    readings: list[GgaReading] = []
    deadline = time.monotonic() + timeout_s

    with serial.Serial(port, baudrate=baud, timeout=1) as device:
        while time.monotonic() < deadline:
            raw_line = device.readline()
            if not raw_line:
                continue

            line = raw_line.decode("ascii", errors="ignore")
            reading = parse_gga(line)
            if reading is None:
                continue

            if reading.fix_quality not in accepted_qualities:
                readings.clear()
                print(
                    f"Waiting for RTK fix: quality={reading.fix_quality}, "
                    f"satellites={reading.satellites}, hdop={reading.hdop}"
                )
                continue

            readings.append(reading)
            print(
                f"Accepted RTK sample {len(readings)}/{samples_required}: "
                f"{reading.latitude:.8f}, {reading.longitude:.8f}"
            )

            if len(readings) >= samples_required:
                return combine_readings(readings)

    mode = "RTK fixed or float" if allow_float else "RTK fixed"
    raise TimeoutError(f"No stable {mode} position received within {timeout_s:g} seconds")
