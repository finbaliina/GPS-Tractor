from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math

import cv2
import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union

from geometry_utils import clean_polygon
from json_io import write_json
from settings import settings


DIRECTION_PERIOD_DEG = 180.0
MAXIMUM_DIRECTION_ERROR_DEG = 90.0
ALIGNMENT_PENALTY_EXPONENT = 2.0
SWATH_EXTENSION_SPACING_MULTIPLIER = 2.0
MINIMUM_LINE_LENGTH_PX = 1e-6
MINIMUM_VECTOR_NORM = 1e-12
MINIMUM_COORDINATE_SPAN = 1e-9
SQUARE_BUFFER_CAP_STYLE = 3
MITRE_BUFFER_JOIN_STYLE = 2


@dataclass(frozen=True)
class ContourRoutePlan:
    working_polygon: Polygon
    swaths: list[LineString]
    metres_per_pixel: float
    working_distance_px: float
    turn_distance_px: float
    turns: int
    estimated_time_s: float
    pass_spacing_m: float
    coverage_percent: float
    angle_deg: float
    contour_alignment_error_deg: float
    mode: str = "contour_straight"

    @property
    def total_distance_m(self) -> float:
        total_distance_px = self.working_distance_px + self.turn_distance_px
        return total_distance_px * self.metres_per_pixel


@dataclass(frozen=True)
class _ContourCandidate:
    score: float
    angle_deg: float
    swaths: list[LineString]
    working_distance_px: float
    turn_distance_px: float
    turns: int
    estimated_time_s: float
    alignment_error_deg: float


def plan_contour_route(
    field: Polygon,
    terrain,
    metres_per_pixel: float,
) -> ContourRoutePlan:
    """Choose straight passes whose global heading best follows terrain contours.

    The passes remain straight. Terrain only changes the preferred overall heading.
    """
    route_settings = settings.route
    contour_settings = settings.contour_route

    total_overlap_fraction = (
        route_settings.pass_overlap_fraction
        + contour_settings.extra_overlap_fraction
    )
    if total_overlap_fraction >= 1.0:
        raise ValueError("Combined route overlap must remain below 1.0.")

    pass_spacing_m = (
        route_settings.implement_width_m * (1.0 - total_overlap_fraction)
    )
    pass_spacing_px = pass_spacing_m / metres_per_pixel
    headland_width_px = route_settings.headland_width_m / metres_per_pixel

    try:
        working_polygon = clean_polygon(field.buffer(-headland_width_px))
    except ValueError as exc:
        raise RuntimeError("Headland is too wide for this field.") from exc

    elevation_grid = np.asarray(terrain.elevation_grid, dtype=np.float64)
    slope_grid_deg = np.asarray(terrain.slope_grid_deg, dtype=np.float64)
    terrain_image_x = np.asarray(terrain.image_x, dtype=np.float64)
    terrain_image_y = np.asarray(terrain.image_y, dtype=np.float64)

    if len(terrain_image_x) < 2 or len(terrain_image_y) < 2:
        raise RuntimeError("Terrain grid is too small to determine contour direction.")

    terrain_step_x_m = float(np.mean(np.diff(terrain_image_x))) * metres_per_pixel
    terrain_step_y_m = float(np.mean(np.diff(terrain_image_y))) * metres_per_pixel
    elevation_gradient_y, elevation_gradient_x = np.gradient(
        elevation_grid,
        terrain_step_y_m,
        terrain_step_x_m,
    )

    working_area_mask = _rasterise_working_polygon(
        working_polygon,
        terrain_image_x=terrain_image_x,
        terrain_image_y=terrain_image_y,
        grid_shape=elevation_grid.shape,
    )
    reliable_terrain_mask = (
        working_area_mask
        & np.isfinite(elevation_gradient_x)
        & np.isfinite(elevation_gradient_y)
        & (slope_grid_deg >= contour_settings.minimum_direction_slope_deg)
    )
    if not np.any(reliable_terrain_mask):
        raise RuntimeError(
            "Terrain is too flat to determine a reliable contour direction."
        )

    best_candidate: _ContourCandidate | None = None
    for angle_deg in np.arange(
        0.0,
        DIRECTION_PERIOD_DEG,
        contour_settings.angle_step_deg,
    ):
        candidate_swaths = _generate_swaths(
            working_polygon,
            angle_deg=float(angle_deg),
            spacing_px=pass_spacing_px,
        )
        if not candidate_swaths:
            continue

        ordered_swaths = _order_swaths_boustrophedon(candidate_swaths)
        candidate = _evaluate_candidate(
            angle_deg=float(angle_deg),
            swaths=ordered_swaths,
            metres_per_pixel=metres_per_pixel,
            elevation_gradient_x=elevation_gradient_x,
            elevation_gradient_y=elevation_gradient_y,
            reliable_terrain_mask=reliable_terrain_mask,
        )
        if best_candidate is None or candidate.score < best_candidate.score:
            best_candidate = candidate

    if best_candidate is None:
        raise RuntimeError("No straight contour route could be generated.")

    coverage_percent = _coverage_percent(
        working_polygon=working_polygon,
        swaths=best_candidate.swaths,
        implement_width_px=route_settings.implement_width_m / metres_per_pixel,
    )

    print(
        f"Straight contour route: {len(best_candidate.swaths)} passes, "
        f"{best_candidate.turns} turns, "
        f"{best_candidate.angle_deg:.1f}°, "
        "mean contour-direction error "
        f"{best_candidate.alignment_error_deg:.1f}°, "
        f"{coverage_percent:.2f}% working-area coverage, "
        f"about {best_candidate.estimated_time_s / 60.0:.1f} min"
    )

    return ContourRoutePlan(
        working_polygon=working_polygon,
        swaths=best_candidate.swaths,
        metres_per_pixel=metres_per_pixel,
        working_distance_px=best_candidate.working_distance_px,
        turn_distance_px=best_candidate.turn_distance_px,
        turns=best_candidate.turns,
        estimated_time_s=best_candidate.estimated_time_s,
        pass_spacing_m=pass_spacing_m,
        coverage_percent=coverage_percent,
        angle_deg=best_candidate.angle_deg,
        contour_alignment_error_deg=best_candidate.alignment_error_deg,
    )


def _evaluate_candidate(
    angle_deg: float,
    swaths: list[LineString],
    metres_per_pixel: float,
    elevation_gradient_x: np.ndarray,
    elevation_gradient_y: np.ndarray,
    reliable_terrain_mask: np.ndarray,
) -> _ContourCandidate:
    route_settings = settings.route
    contour_settings = settings.contour_route

    working_distance_px = sum(swath.length for swath in swaths)
    turn_distance_px = sum(
        math.dist(current.coords[-1], following.coords[0])
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

    alignment_error_deg = _contour_alignment_error(
        angle_deg=angle_deg,
        elevation_gradient_x=elevation_gradient_x,
        elevation_gradient_y=elevation_gradient_y,
        reliable_terrain_mask=reliable_terrain_mask,
    )
    alignment_fraction = alignment_error_deg / MAXIMUM_DIRECTION_ERROR_DEG
    route_score = estimated_time_s * (
        1.0
        + contour_settings.alignment_weight
        * alignment_fraction**ALIGNMENT_PENALTY_EXPONENT
    )

    return _ContourCandidate(
        score=route_score,
        angle_deg=angle_deg,
        swaths=swaths,
        working_distance_px=working_distance_px,
        turn_distance_px=turn_distance_px,
        turns=turn_count,
        estimated_time_s=estimated_time_s,
        alignment_error_deg=alignment_error_deg,
    )


def save_contour_route_plan(plan: ContourRoutePlan, path: Path) -> None:
    write_json(
        path,
        {
            "mode": plan.mode,
            "angle_deg": plan.angle_deg,
            "passes": len(plan.swaths),
            "turns": plan.turns,
            "pass_spacing_m": plan.pass_spacing_m,
            "coverage_percent": plan.coverage_percent,
            "mean_contour_direction_error_deg": plan.contour_alignment_error_deg,
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


def _generate_swaths(
    working_polygon: Polygon,
    angle_deg: float,
    spacing_px: float,
) -> list[LineString]:
    """Rotate, slice with straight lines, then rotate clipped swaths back."""
    field_centre = working_polygon.centroid
    rotated_polygon = affinity.rotate(
        working_polygon,
        -angle_deg,
        origin=(field_centre.x, field_centre.y),
        use_radians=False,
    )
    minimum_x, minimum_y, maximum_x, maximum_y = rotated_polygon.bounds

    swath_y = minimum_y + spacing_px / 2.0
    line_extension_px = (
        max(maximum_x - minimum_x, maximum_y - minimum_y)
        + spacing_px * SWATH_EXTENSION_SPACING_MULTIPLIER
    )
    rotated_swaths: list[LineString] = []

    while swath_y <= maximum_y:
        cutting_line = LineString(
            [
                (minimum_x - line_extension_px, swath_y),
                (maximum_x + line_extension_px, swath_y),
            ]
        )
        clipped_geometry = cutting_line.intersection(rotated_polygon)
        rotated_swaths.extend(
            line_part
            for line_part in _line_parts(clipped_geometry)
            if line_part.length > MINIMUM_LINE_LENGTH_PX
        )
        swath_y += spacing_px

    return [
        affinity.rotate(
            swath,
            angle_deg,
            origin=(field_centre.x, field_centre.y),
            use_radians=False,
        )
        for swath in rotated_swaths
    ]


def _order_swaths_boustrophedon(swaths: list[LineString]) -> list[LineString]:
    """Order parallel lines spatially and alternate travel direction."""
    if not swaths:
        return []

    first_swath = swaths[0]
    first_start_x, first_start_y = first_swath.coords[0]
    first_end_x, first_end_y = first_swath.coords[-1]
    direction_x = first_end_x - first_start_x
    direction_y = first_end_y - first_start_y
    direction_length = math.hypot(direction_x, direction_y)
    if direction_length < MINIMUM_COORDINATE_SPAN:
        return swaths

    normal_x = -direction_y / direction_length
    normal_y = direction_x / direction_length
    spatially_ordered_swaths = sorted(
        swaths,
        key=lambda swath: (
            swath.centroid.x * normal_x + swath.centroid.y * normal_y
        ),
    )

    ordered_swaths: list[LineString] = []
    for swath_index, swath in enumerate(spatially_ordered_swaths):
        coordinates = list(swath.coords)

        if swath_index == 0:
            if coordinates[0][0] > coordinates[-1][0]:
                coordinates.reverse()
        else:
            previous_end = ordered_swaths[-1].coords[-1]
            distance_to_start = math.dist(previous_end, coordinates[0])
            distance_to_end = math.dist(previous_end, coordinates[-1])
            if distance_to_end < distance_to_start:
                coordinates.reverse()

        ordered_swaths.append(LineString(coordinates))

    return ordered_swaths


def _contour_alignment_error(
    angle_deg: float,
    elevation_gradient_x: np.ndarray,
    elevation_gradient_y: np.ndarray,
    reliable_terrain_mask: np.ndarray,
) -> float:
    """Return mean angle between a proposed pass and local equal-elevation direction."""
    candidate_angle_rad = math.radians(angle_deg)
    candidate_direction_x = math.cos(candidate_angle_rad)
    candidate_direction_y = math.sin(candidate_angle_rad)

    gradient_x = elevation_gradient_x[reliable_terrain_mask]
    gradient_y = elevation_gradient_y[reliable_terrain_mask]
    gradient_magnitude = np.hypot(gradient_x, gradient_y)
    nonzero_gradient = gradient_magnitude > MINIMUM_VECTOR_NORM
    if not np.any(nonzero_gradient):
        return MAXIMUM_DIRECTION_ERROR_DEG

    unit_gradient_x = gradient_x[nonzero_gradient] / gradient_magnitude[nonzero_gradient]
    unit_gradient_y = gradient_y[nonzero_gradient] / gradient_magnitude[nonzero_gradient]

    # Equal-elevation direction is perpendicular to the uphill gradient.
    contour_direction_x = -unit_gradient_y
    contour_direction_y = unit_gradient_x
    absolute_dot_product = np.abs(
        contour_direction_x * candidate_direction_x
        + contour_direction_y * candidate_direction_y
    )
    absolute_dot_product = np.clip(absolute_dot_product, 0.0, 1.0)
    angular_errors_deg = np.degrees(np.arccos(absolute_dot_product))

    # Steeper cells carry more weight because both the direction estimate and the
    # agricultural importance of contour-following are stronger there.
    gradient_weights = gradient_magnitude[nonzero_gradient]
    mean_gradient_weight = max(
        float(np.mean(gradient_weights)),
        MINIMUM_VECTOR_NORM,
    )
    normalised_weights = gradient_weights / mean_gradient_weight
    return float(np.average(angular_errors_deg, weights=normalised_weights))


def _rasterise_working_polygon(
    working_polygon: Polygon,
    terrain_image_x: np.ndarray,
    terrain_image_y: np.ndarray,
    grid_shape: tuple[int, int],
) -> np.ndarray:
    grid_height, grid_width = grid_shape
    mask = np.zeros((grid_height, grid_width), dtype=np.uint8)

    image_x_minimum = float(terrain_image_x[0])
    image_x_maximum = float(terrain_image_x[-1])
    image_y_minimum = float(terrain_image_y[0])
    image_y_maximum = float(terrain_image_y[-1])
    scale_x = (grid_width - 1) / max(
        image_x_maximum - image_x_minimum,
        MINIMUM_COORDINATE_SPAN,
    )
    scale_y = (grid_height - 1) / max(
        image_y_maximum - image_y_minimum,
        MINIMUM_COORDINATE_SPAN,
    )

    polygons = (
        [working_polygon]
        if working_polygon.geom_type == "Polygon"
        else list(working_polygon.geoms)
    )
    for polygon in polygons:
        exterior_points = np.asarray(polygon.exterior.coords, dtype=float).copy()
        exterior_points[:, 0] = (
            exterior_points[:, 0] - image_x_minimum
        ) * scale_x
        exterior_points[:, 1] = (
            exterior_points[:, 1] - image_y_minimum
        ) * scale_y
        cv2.fillPoly(mask, [np.rint(exterior_points).astype(np.int32)], 1)

        for interior_ring in polygon.interiors:
            hole_points = np.asarray(interior_ring.coords, dtype=float).copy()
            hole_points[:, 0] = (hole_points[:, 0] - image_x_minimum) * scale_x
            hole_points[:, 1] = (hole_points[:, 1] - image_y_minimum) * scale_y
            cv2.fillPoly(mask, [np.rint(hole_points).astype(np.int32)], 0)

    return mask.astype(bool)


def _coverage_percent(
    working_polygon: Polygon,
    swaths: list[LineString],
    implement_width_px: float,
) -> float:
    if working_polygon.area <= 0.0 or not swaths:
        return 0.0

    half_implement_width_px = implement_width_px / 2.0
    covered_strips = [
        swath.buffer(
            half_implement_width_px,
            cap_style=SQUARE_BUFFER_CAP_STYLE,
            join_style=MITRE_BUFFER_JOIN_STYLE,
        )
        for swath in swaths
    ]
    covered_area = unary_union(covered_strips).intersection(working_polygon)
    return float(100.0 * covered_area.area / working_polygon.area)


def _line_parts(geometry) -> list[LineString]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    if hasattr(geometry, "geoms"):
        return [
            part for part in geometry.geoms if isinstance(part, LineString)
        ]
    return []
