from __future__ import annotations

from dataclasses import asdict, dataclass
import statistics
import time

from settings import settings


# NMEA/GGA protocol constants, not tuning parameters.
NMEA_GGA_MINIMUM_FIELD_COUNT = 10
NMEA_RTK_FIXED_QUALITY = 4
NMEA_RTK_FLOAT_QUALITY = 5
MINUTES_PER_DEGREE = 60.0


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

    def as_dict(self) -> dict:
        return asdict(self)


def get_position() -> Position:
    """Return either the configured mock position or a stable RTK position."""
    gps_settings = settings.gps

    if gps_settings.mode == "mock":
        print(
            "Using mock position: "
            f"{gps_settings.mock_latitude:.8f}, {gps_settings.mock_longitude:.8f}"
        )
        return Position(
            latitude=gps_settings.mock_latitude,
            longitude=gps_settings.mock_longitude,
            source="mock",
            fix_quality=NMEA_RTK_FIXED_QUALITY,
        )

    if gps_settings.mode != "rtk":
        raise ValueError("GPS_MODE must be either 'mock' or 'rtk'.")

    return _wait_for_rtk(
        serial_port=gps_settings.serial_port,
        baud_rate=gps_settings.baud_rate,
        samples_required=gps_settings.stable_samples_required,
        overall_timeout_s=gps_settings.fix_timeout_s,
        serial_read_timeout_s=gps_settings.serial_read_timeout_s,
        allow_float_rtk=gps_settings.allow_float_rtk,
    )


def _wait_for_rtk(
    serial_port: str,
    baud_rate: int,
    samples_required: int,
    overall_timeout_s: float,
    serial_read_timeout_s: float,
    allow_float_rtk: bool,
) -> Position:
    import serial

    acceptable_fix_qualities = {NMEA_RTK_FIXED_QUALITY}
    if allow_float_rtk:
        acceptable_fix_qualities.add(NMEA_RTK_FLOAT_QUALITY)

    accepted_readings: list[dict] = []
    deadline = time.monotonic() + overall_timeout_s

    print(f"Reading NMEA from {serial_port} at {baud_rate} baud...")

    with serial.Serial(
        serial_port,
        baudrate=baud_rate,
        timeout=serial_read_timeout_s,
    ) as gps_device:
        while time.monotonic() < deadline:
            nmea_sentence = gps_device.readline().decode("ascii", errors="ignore")
            parsed_reading = _parse_gga(nmea_sentence)
            if parsed_reading is None:
                continue

            if parsed_reading["fix_quality"] not in acceptable_fix_qualities:
                accepted_readings.clear()
                continue

            accepted_readings.append(parsed_reading)
            print(f"RTK sample {len(accepted_readings)}/{samples_required}")

            if len(accepted_readings) >= samples_required:
                return _combine_readings(accepted_readings)

    raise TimeoutError(
        f"No stable RTK position received within {overall_timeout_s:g} seconds"
    )


def _parse_gga(nmea_line: str) -> dict | None:
    sentence = nmea_line.strip()
    if not sentence.startswith("$"):
        return None

    sentence_without_checksum = sentence[1:].split("*", 1)[0]
    fields = sentence_without_checksum.split(",")

    if (
        len(fields) < NMEA_GGA_MINIMUM_FIELD_COUNT
        or not fields[0].endswith("GGA")
    ):
        return None

    try:
        latitude = _nmea_coordinate(fields[2], fields[3])
        longitude = _nmea_coordinate(fields[4], fields[5])
        fix_quality = int(fields[6] or 0)
    except (ValueError, IndexError):
        return None

    if latitude == 0.0 and longitude == 0.0:
        return None

    return {
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": _float_or_none(fields[9]),
        "fix_quality": fix_quality,
        "satellites": _int_or_none(fields[7]),
        "hdop": _float_or_none(fields[8]),
    }


def _combine_readings(readings: list[dict]) -> Position:
    def median_value(key: str):
        available_values = [
            reading[key] for reading in readings if reading[key] is not None
        ]
        return statistics.median(available_values) if available_values else None

    satellite_counts = [
        reading["satellites"]
        for reading in readings
        if reading["satellites"] is not None
    ]

    return Position(
        latitude=statistics.median(reading["latitude"] for reading in readings),
        longitude=statistics.median(reading["longitude"] for reading in readings),
        source="nmea_gga",
        altitude_m=median_value("altitude_m"),
        fix_quality=min(reading["fix_quality"] for reading in readings),
        satellites=min(satellite_counts) if satellite_counts else None,
        hdop=median_value("hdop"),
        samples=len(readings),
    )


def _nmea_coordinate(value: str, hemisphere: str) -> float:
    degree_digit_count = 2 if hemisphere in {"N", "S"} else 3
    degrees = float(value[:degree_digit_count])
    minutes = float(value[degree_digit_count:])
    decimal_degrees = degrees + minutes / MINUTES_PER_DEGREE
    return -decimal_degrees if hemisphere in {"S", "W"} else decimal_degrees


def _float_or_none(value: str) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _int_or_none(value: str) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
