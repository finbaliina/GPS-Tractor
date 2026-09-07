from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
import statistics
import time


@dataclass(frozen=True)
class Position:
    latitude: float
    longitude: float
    source: str
    altitude_m: float | None = None
    fix_quality: int | None = None
    satellites: int | None = None
    hdop: float | None = None
    samples: int = 1

    def as_dict(self):
        return asdict(self)


def get_position() -> Position:
    """Return either a mock position or a stable RTK position."""
    mode = os.getenv("GPS_MODE", "mock").lower()

    if mode == "mock":
        lat = float(os.environ["MOCK_LAT"])
        lon = float(os.environ["MOCK_LON"])
        print(f"Using mock position: {lat:.8f}, {lon:.8f}")
        return Position(lat, lon, "mock", fix_quality=4)

    return _wait_for_rtk(
        port=os.getenv("GPS_PORT", "/dev/serial0"),
        baud=int(os.getenv("GPS_BAUD", "115200")),
        samples_required=int(os.getenv("GPS_SAMPLES", "5")),
        timeout_s=float(os.getenv("GPS_TIMEOUT", "300")),
        allow_float=_env_bool("GPS_ALLOW_FLOAT", False),
    )


def _wait_for_rtk(
    port: str,
    baud: int,
    samples_required: int,
    timeout_s: float,
    allow_float: bool,
) -> Position:
    import serial

    accepted = {4, 5} if allow_float else {4}
    readings = []
    deadline = time.monotonic() + timeout_s

    print(f"Reading NMEA from {port} at {baud} baud...")

    with serial.Serial(port, baudrate=baud, timeout=1) as device:
        while time.monotonic() < deadline:
            reading = _parse_gga(device.readline().decode("ascii", errors="ignore"))
            if reading is None:
                continue

            if reading["fix_quality"] not in accepted:
                readings.clear()
                continue

            readings.append(reading)
            print(f"RTK sample {len(readings)}/{samples_required}")

            if len(readings) >= samples_required:
                return _combine(readings)

    raise TimeoutError(f"No stable RTK position received within {timeout_s:g} seconds")


def _parse_gga(line: str):
    sentence = line.strip()
    if not sentence.startswith("$"):
        return None

    content = sentence[1:].split("*", 1)[0]
    fields = content.split(",")

    if len(fields) < 10 or not fields[0].endswith("GGA"):
        return None

    try:
        lat = _nmea_coordinate(fields[2], fields[3])
        lon = _nmea_coordinate(fields[4], fields[5])
        quality = int(fields[6] or 0)
    except (ValueError, IndexError):
        return None

    if lat == 0 and lon == 0:
        return None

    return {
        "latitude": lat,
        "longitude": lon,
        "altitude_m": _float_or_none(fields[9]),
        "fix_quality": quality,
        "satellites": _int_or_none(fields[7]),
        "hdop": _float_or_none(fields[8]),
    }


def _combine(readings) -> Position:
    def median(key):
        values = [r[key] for r in readings if r[key] is not None]
        return statistics.median(values) if values else None

    return Position(
        latitude=statistics.median(r["latitude"] for r in readings),
        longitude=statistics.median(r["longitude"] for r in readings),
        source="nmea_gga",
        altitude_m=median("altitude_m"),
        fix_quality=min(r["fix_quality"] for r in readings),
        satellites=min(
            (r["satellites"] for r in readings if r["satellites"] is not None),
            default=None,
        ),
        hdop=median("hdop"),
        samples=len(readings),
    )


def _nmea_coordinate(value: str, hemisphere: str) -> float:
    degree_digits = 2 if hemisphere in {"N", "S"} else 3
    degrees = float(value[:degree_digits])
    minutes = float(value[degree_digits:])
    result = degrees + minutes / 60
    return -result if hemisphere in {"S", "W"} else result


def _float_or_none(value):
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _int_or_none(value):
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}
