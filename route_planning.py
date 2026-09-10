from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon

from geometry_utils import clean_polygon
from json_io import write_json
from settings import PROJECT_ROOT, settings


TRACTOR_PROFILE_FILE = PROJECT_ROOT / "tractor_profiles.json"
DIRECTION_PERIOD_DEG = 180.0


@dataclass(frozen=True)
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
    def total_distance_m(self) -> float:
        total_distance_px = self.working_distance_px + self.turn_distance_px
        return total_distance_px * self.metres_per_pixel


@dataclass(frozen=True)
class _RouteCandidate:
    angle_deg: float
    swaths: list[LineString]
    working_distance_px: float
    turn_distance_px: float
    turns: int
    estimated_time_s: float


def plan_route(field: Polygon, metres_per_pixel: float) -> RoutePlan:
    """Choose the lowest estimated-time parallel coverage direction."""
    route_settings = settings.route
    _print_field_size(field, metres_per_pixel)

    effective_pass_spacing_m = (
        route_settings.implement_width_m
        * (1.0 - route_settings.pass_overlap_fraction)
    )
    pass_spacing_px = effective_pass_spacing_m / metres_per_pixel
    headland_width_px = route_settings.headland_width_m / metres_per_pixel

    tractor = _load_tractor_profile(route_settings.tractor_profile)
    turning_radius_px = tractor["turn_radius_m"] / metres_per_pixel

    try:
        working_polygon = clean_polygon(field.buffer(-headland_width_px))
    except ValueError as exc:
        raise RuntimeError("Headland is too wide for this field.") from exc

    best_candidate: _RouteCandidate | None = None
    candidate_angle_deg = 0.0

    while candidate_angle_deg < DIRECTION_PERIOD_DEG:
        candidate_swaths = _generate_swaths(
            working_polygon,
            angle_deg=candidate_angle_deg,
            spacing_px=pass_spacing_px,
        )
        if candidate_swaths:
            ordered_swaths = _order_swaths_boustrophedon(
                candidate_swaths,
                working_polygon,
                candidate_angle_deg,
            )
            candidate = _evaluate_route_candidate(
                angle_deg=candidate_angle_deg,
                swaths=ordered_swaths,
                metres_per_pixel=metres_per_pixel,
                turning_radius_px=turning_radius_px,
            )
            if (
                best_candidate is None
                or candidate.estimated_time_s < best_candidate.estimated_time_s
            ):
                best_candidate = candidate

        candidate_angle_deg += route_settings.straight_angle_step_deg

    if best_candidate is None:
        raise RuntimeError("No route could be generated.")

    plan = RoutePlan(
        angle_deg=best_candidate.angle_deg,
        working_polygon=working_polygon,
        swaths=best_candidate.swaths,
        metres_per_pixel=metres_per_pixel,
        working_distance_px=best_candidate.working_distance_px,
        turn_distance_px=best_candidate.turn_distance_px,
        turns=best_candidate.turns,
        estimated_time_s=best_candidate.estimated_time_s,
    )
    print(
        f"Route: {len(plan.swaths)} passes, {plan.turns} turns, "
        f"{plan.angle_deg:.1f}°, about {plan.estimated_time_s / 60.0:.1f} min"
    )
    return plan


def _evaluate_route_candidate(
    angle_deg: float,
    swaths: list[LineString],
    metres_per_pixel: float,
    turning_radius_px: float,
) -> _RouteCandidate:
    route_settings = settings.route
    working_distance_px = sum(swath.length for swath in swaths)
    turn_distance_px = sum(
        max(
            math.dist(current.coords[-1], following.coords[0]),
            math.pi * turning_radius_px,
        )
        for current, following in zip(swaths, swaths[1:])
    )
    turn_count = max(0, len(swaths) - 1)

    estimated_time_s = (
        working_distance_px
        * metres_per_pixel
        / route_settings.working_speed_mps
        + turn_distance_px
        * metres_per_pixel
        / route_settings.turning_speed_mps
        + turn_count * route_settings.fixed_turn_time_s
    )
    return _RouteCandidate(
        angle_deg=angle_deg,
        swaths=swaths,
        working_distance_px=working_distance_px,
        turn_distance_px=turn_distance_px,
        turns=turn_count,
        estimated_time_s=estimated_time_s,
    )


def save_route_plan(plan: RoutePlan, path: Path) -> None:
    write_json(
        path,
        {
            "angle_deg": plan.angle_deg,
            "passes": len(plan.swaths),
            "turns": plan.turns,
            "working_distance_m": plan.working_distance_px * plan.metres_per_pixel,
            "turn_distance_m": plan.turn_distance_px * plan.metres_per_pixel,
            "total_distance_m": plan.total_distance_m,
            "estimated_time_min": plan.estimated_time_s / 60.0,
            "swaths_pixels": [
                [[float(x), float(y)] for x, y in swath.coords]
                for swath in plan.swaths
            ],
        },
    )


def _load_tractor_profile(profile_name: str) -> dict[str, float | bool]:
    profiles = json.loads(TRACTOR_PROFILE_FILE.read_text(encoding="utf-8"))
    if profile_name not in profiles:
        raise KeyError(f"Unknown tractor profile: {profile_name}")

    profile = profiles[profile_name]
    turning_radius_m = profile.get("minimum_turning_radius_m")
    should_calculate_radius = (
        not profile.get("use_measured_turning_radius", True)
        or turning_radius_m is None
    )
    if should_calculate_radius:
        turning_radius_m = profile["wheelbase_m"] / math.tan(
            math.radians(profile["max_steering_angle_deg"])
        )

    return {
        "turn_radius_m": float(turning_radius_m),
        "can_reverse": bool(profile["can_reverse"]),
    }


def _generate_swaths(
    working_polygon: Polygon,
    angle_deg: float,
    spacing_px: float,
) -> list[LineString]:
    field_centre = working_polygon.centroid
    rotated_polygon = affinity.rotate(
        working_polygon,
        -angle_deg,
        origin=field_centre,
    )
    minimum_x, minimum_y, maximum_x, maximum_y = rotated_polygon.bounds
    cutting_line_extension_px = max(
        maximum_x - minimum_x,
        maximum_y - minimum_y,
    )

    rotated_swaths: list[LineString] = []
    swath_y = minimum_y + spacing_px / 2.0
    while swath_y <= maximum_y:
        cutting_line = LineString(
            [
                (minimum_x - cutting_line_extension_px, swath_y),
                (maximum_x + cutting_line_extension_px, swath_y),
            ]
        )
        clipped_geometry = rotated_polygon.intersection(cutting_line)
        if isinstance(clipped_geometry, LineString):
            line_parts = [clipped_geometry]
        elif isinstance(clipped_geometry, MultiLineString):
            line_parts = list(clipped_geometry.geoms)
        else:
            line_parts = []

        rotated_swaths.extend(
            line_part for line_part in line_parts if line_part.length > 0.0
        )
        swath_y += spacing_px

    return [
        affinity.rotate(swath, angle_deg, origin=field_centre)
        for swath in rotated_swaths
    ]


def _order_swaths_boustrophedon(
    swaths: list[LineString],
    working_polygon: Polygon,
    angle_deg: float,
) -> list[LineString]:
    field_centre = working_polygon.centroid
    spatially_ordered_swaths = sorted(
        swaths,
        key=lambda swath: affinity.rotate(
            swath,
            -angle_deg,
            origin=field_centre,
        ).centroid.y,
    )

    ordered_swaths: list[LineString] = []
    for swath_index, swath in enumerate(spatially_ordered_swaths):
        coordinates = list(swath.coords)
        if swath_index % 2 == 1:
            coordinates.reverse()
        ordered_swaths.append(LineString(coordinates))
    return ordered_swaths


def _print_field_size(field: Polygon, metres_per_pixel: float) -> None:
    field_area_m2 = field.area * metres_per_pixel**2
    minimum_x, minimum_y, maximum_x, maximum_y = field.bounds
    field_width_m = (maximum_x - minimum_x) * metres_per_pixel
    field_height_m = (maximum_y - minimum_y) * metres_per_pixel
    print(
        "Detected field: approximately "
        f"{field_width_m:.1f} m × {field_height_m:.1f} m, "
        f"{field_area_m2:.0f} m²"
    )
