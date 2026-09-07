from dataclasses import dataclass
from pathlib import Path
import json
import math
import os

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon


PROFILE_FILE = Path(__file__).with_name("tractor_profiles.json")


@dataclass
class RoutePlan:
    angle_deg: float
    working_polygon: Polygon
    swaths: list[LineString]
    metres_per_pixel: float
    working_distance_px: float
    turn_distance_px: float
    turns: int
    estimated_time_s: float

    @property
    def total_distance_m(self):
        return (self.working_distance_px + self.turn_distance_px) * self.metres_per_pixel


def plan_route(field: Polygon, metres_per_pixel: float) -> RoutePlan:
    """Choose the lowest-cost parallel coverage direction."""
    implement_m = float(os.getenv("IMPLEMENT_WIDTH_M", "6"))
    headland_m = float(os.getenv("HEADLAND_WIDTH_M", "10"))
    overlap = float(os.getenv("OVERLAP", "0.02"))
    angle_step = float(os.getenv("ANGLE_STEP_DEG", "2"))
    work_speed = float(os.getenv("WORKING_SPEED_MPS", "2.5"))
    turn_speed = float(os.getenv("TURNING_SPEED_MPS", "1.5"))
    fixed_turn_s = float(os.getenv("FIXED_TURN_TIME_S", "2"))

    tractor = _tractor(os.getenv("TRACTOR_PROFILE", "default_tractor"))
    px_per_m = 1 / metres_per_pixel

    implement = implement_m * px_per_m * (1 - overlap)
    headland = headland_m * px_per_m
    turn_radius = tractor["turn_radius_m"] * px_per_m

    working = field.buffer(-headland)
    if working.is_empty:
        raise RuntimeError("Headland is too wide for this field.")
    if not isinstance(working, Polygon):
        working = max(working.geoms, key=lambda p: p.area)

    best = None

    angle = 0.0
    while angle < 180:
        swaths = _swaths(working, angle, implement)
        if swaths:
            swaths = _order(swaths, working, angle)
            work_px = sum(line.length for line in swaths)
            turn_px = sum(
                max(
                    math.dist(swaths[i].coords[-1], swaths[i + 1].coords[0]),
                    math.pi * turn_radius,
                )
                for i in range(len(swaths) - 1)
            )
            turns = max(0, len(swaths) - 1)

            time_s = (
                work_px * metres_per_pixel / work_speed
                + turn_px * metres_per_pixel / turn_speed
                + turns * fixed_turn_s
            )

            if best is None or time_s < best.estimated_time_s:
                best = RoutePlan(
                    angle, working, swaths, metres_per_pixel,
                    work_px, turn_px, turns, time_s
                )

        angle += angle_step

    if best is None:
        raise RuntimeError("No route could be generated.")

    print(
        f"Route: {len(best.swaths)} passes, {best.turns} turns, "
        f"{best.angle_deg:.1f}°, about {best.estimated_time_s / 60:.1f} min"
    )
    return best


def save_route_plan(plan: RoutePlan, path: Path):
    path.write_text(
        json.dumps(
            {
                "angle_deg": plan.angle_deg,
                "passes": len(plan.swaths),
                "turns": plan.turns,
                "working_distance_m": plan.working_distance_px * plan.metres_per_pixel,
                "turn_distance_m": plan.turn_distance_px * plan.metres_per_pixel,
                "total_distance_m": plan.total_distance_m,
                "estimated_time_min": plan.estimated_time_s / 60,
                "swaths_pixels": [
                    [[float(x), float(y)] for x, y in line.coords]
                    for line in plan.swaths
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _tractor(name):
    profiles = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    data = profiles[name]

    radius = data.get("minimum_turning_radius_m")
    if not data.get("use_measured_turning_radius", True) or radius is None:
        radius = data["wheelbase_m"] / math.tan(
            math.radians(data["max_steering_angle_deg"])
        )

    return {"turn_radius_m": float(radius), "can_reverse": bool(data["can_reverse"])}


def _swaths(field: Polygon, angle: float, spacing: float):
    centre = field.centroid
    rotated = affinity.rotate(field, -angle, origin=centre)
    min_x, min_y, max_x, max_y = rotated.bounds
    extra = max(max_x - min_x, max_y - min_y)

    lines = []
    y = min_y + spacing / 2

    while y <= max_y:
        cut = rotated.intersection(
            LineString([(min_x - extra, y), (max_x + extra, y)])
        )
        parts = [cut] if isinstance(cut, LineString) else (
            list(cut.geoms) if isinstance(cut, MultiLineString) else []
        )
        lines.extend(line for line in parts if line.length > 0)
        y += spacing

    return [affinity.rotate(line, angle, origin=centre) for line in lines]


def _order(swaths, field, angle):
    centre = field.centroid
    ordered = sorted(
        swaths,
        key=lambda line: affinity.rotate(line, -angle, origin=centre).centroid.y,
    )

    result = []
    for i, line in enumerate(ordered):
        coords = list(line.coords)
        if i % 2:
            coords.reverse()
        result.append(LineString(coords))
    return result
