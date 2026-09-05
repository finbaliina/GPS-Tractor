from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class Position:
    latitude: float
    longitude: float
    altitude_m: float | None
    fix_quality: int
    satellites: int | None
    hdop: float | None
    samples: int
    acquired_at: str

    @classmethod
    def mock(cls, latitude: float, longitude: float) -> "Position":
        return cls(
            latitude=latitude,
            longitude=longitude,
            altitude_m=None,
            fix_quality=4,
            satellites=None,
            hdop=None,
            samples=1,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

