from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
import os

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon


@dataclass
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
    def total_distance_m(self):
        return (self.working_distance_px + self.turn_distance_px) * self.metres_per_pixel


def plan_contour_route(
    field: Polygon,
    terrain,
    metres_per_pixel: float,
) -> ContourRoutePlan:
    """
    Straight-line contour planner.

    The passes themselves are always straight.

    The planner tests many possible straight-line headings and chooses the
    heading which best follows the local equal-elevation direction across the
    field, while still taking route length / number of turns into account.

    In other words:
        terrain decides the best global direction;
        implement width decides line spacing;
        field polygon decides where each straight line starts and stops.
    """
    implement_m = float(os.getenv("IMPLEMENT_WIDTH_M", "6"))
    headland_m = float(os.getenv("HEADLAND_WIDTH_M", "10"))
    overlap = float(os.getenv("OVERLAP", "0.02"))

    angle_step_deg = max(
        0.5, float(os.getenv("CONTOUR_STRAIGHT_ANGLE_STEP_DEG", "1.0"))
    )

    work_speed = float(os.getenv("WORKING_SPEED_MPS", "2.5"))
    turn_speed = float(os.getenv("TURNING_SPEED_MPS", "1.5"))
    fixed_turn_s = float(os.getenv("FIXED_TURN_TIME_S", "2"))

    # How strongly terrain alignment matters relative to route efficiency.
    # Higher = more willing to accept extra turns/distance to remain on contour.
    alignment_weight = max(
        0.0, float(os.getenv("CONTOUR_ALIGNMENT_WEIGHT", "8.0"))
    )

    # Ignore nearly-flat DEM cells when deciding contour direction because
    # their gradient direction is unstable/noisy.
    min_gradient_slope_deg = max(
        0.0, float(os.getenv("CONTOUR_DIRECTION_MIN_SLOPE_DEG", "1.0"))
    )

    extra_overlap = max(
        0.0,
        min(0.50, float(os.getenv("CONTOUR_EXTRA_OVERLAP", "0.08")))
    )
    total_overlap = min(0.60, overlap + extra_overlap)

    spacing_m = implement_m * (1.0 - total_overlap)
    spacing_px = spacing_m / metres_per_pixel
    headland_px = headland_m / metres_per_pixel

    if spacing_px <= 0:
        raise RuntimeError("Implement width / overlap produced invalid pass spacing.")

    working = field.buffer(-headland_px)

    if working.is_empty:
        raise RuntimeError("Headland is too wide for this field.")

    if working.geom_type == "MultiPolygon":
        working = max(working.geoms, key=lambda p: p.area)

    # Work out the local slope direction from the DEM.
    elevation = np.asarray(terrain.elevation_grid, dtype=np.float64)
    slope_grid = np.asarray(terrain.slope_grid_deg, dtype=np.float64)

    image_x = np.asarray(terrain.image_x, dtype=np.float64)
    image_y = np.asarray(terrain.image_y, dtype=np.float64)

    dx_px = float(np.mean(np.diff(image_x))) if len(image_x) > 1 else 1.0
    dy_px = float(np.mean(np.diff(image_y))) if len(image_y) > 1 else 1.0

    dx_m = dx_px * metres_per_pixel
    dy_m = dy_px * metres_per_pixel

    dz_dy, dz_dx = np.gradient(elevation, dy_m, dx_m)

    # Restrict terrain scoring to the working polygon.
    mask = _working_mask(
        working,
        image_x=image_x,
        image_y=image_y,
        shape=elevation.shape,
    )

    useful = mask & np.isfinite(dz_dx) & np.isfinite(dz_dy)
    useful &= slope_grid >= min_gradient_slope_deg

    if not np.any(useful):
        raise RuntimeError(
            "Terrain is too flat to determine a reliable contour direction."
        )

    best = None

    for angle_deg in np.arange(0.0, 180.0, angle_step_deg):
        swaths = _make_swaths(
            working,
            angle_deg=float(angle_deg),
            spacing_px=spacing_px,
        )

        if not swaths:
            continue

        swaths = _order_boustrophedon(swaths)

        working_px = sum(line.length for line in swaths)
        turn_px = sum(
            math.dist(swaths[i].coords[-1], swaths[i + 1].coords[0])
            for i in range(len(swaths) - 1)
        )
        turns = max(0, len(swaths) - 1)

        estimated_time_s = (
            working_px * metres_per_pixel / work_speed
            + turn_px * metres_per_pixel / turn_speed
            + turns * fixed_turn_s
        )

        alignment_error_deg = _contour_alignment_error(
            angle_deg=float(angle_deg),
            dz_dx=dz_dx,
            dz_dy=dz_dy,
            useful_mask=useful,
        )

        # Convert the terrain error into a multiplier on route time.
        #
        # 0° error => no penalty.
        # 90° error => maximum penalty.
        alignment_fraction = alignment_error_deg / 90.0
        score = estimated_time_s * (
            1.0 + alignment_weight * alignment_fraction ** 2
        )

        candidate = {
            "score": score,
            "angle_deg": float(angle_deg),
            "swaths": swaths,
            "working_px": working_px,
            "turn_px": turn_px,
            "turns": turns,
            "time_s": estimated_time_s,
            "alignment_error_deg": alignment_error_deg,
        }

        if best is None or candidate["score"] < best["score"]:
            best = candidate

    if best is None:
        raise RuntimeError("No straight contour route could be generated.")

    coverage_percent = _coverage_percent(
        working=working,
        swaths=best["swaths"],
        implement_width_px=implement_m / metres_per_pixel,
    )

    print(
        f"Straight contour route: {len(best['swaths'])} passes, "
        f"{best['turns']} turns, "
        f"{best['angle_deg']:.1f}°, "
        f"mean contour-direction error {best['alignment_error_deg']:.1f}°, "
        f"{coverage_percent:.2f}% working-area coverage, "
        f"about {best['time_s'] / 60:.1f} min"
    )

    return ContourRoutePlan(
        working_polygon=working,
        swaths=best["swaths"],
        metres_per_pixel=metres_per_pixel,
        working_distance_px=best["working_px"],
        turn_distance_px=best["turn_px"],
        turns=best["turns"],
        estimated_time_s=best["time_s"],
        pass_spacing_m=spacing_m,
        coverage_percent=coverage_percent,
        angle_deg=best["angle_deg"],
        contour_alignment_error_deg=best["alignment_error_deg"],
    )


def save_contour_route_plan(plan: ContourRoutePlan, path: Path):
    path.write_text(
        json.dumps(
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
                "estimated_time_min": plan.estimated_time_s / 60,
                "swaths_pixels": [
                    [[float(x), float(y)] for x, y in line.coords]
                    for line in plan.swaths
                ],
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _make_swaths(
    working: Polygon,
    angle_deg: float,
    spacing_px: float,
) -> list[LineString]:
    """
    Rotate the field so candidate passes are horizontal, slice it with evenly
    spaced straight lines, then rotate the clipped passes back.
    """
    centre = working.centroid

    rotated = affinity.rotate(
        working,
        -angle_deg,
        origin=(centre.x, centre.y),
        use_radians=False,
    )

    minx, miny, maxx, maxy = rotated.bounds

    # Start half a spacing inside the lower bound. This gives implement coverage
    # roughly half a width beyond the first/last centreline.
    y = miny + spacing_px / 2.0

    swaths_rotated = []

    margin = max(maxx - minx, maxy - miny) + spacing_px * 2

    while y <= maxy:
        candidate = LineString([
            (minx - margin, y),
            (maxx + margin, y),
        ])

        clipped = candidate.intersection(rotated)

        for part in _line_parts(clipped):
            if part.length > 1e-6:
                swaths_rotated.append(part)

        y += spacing_px

    swaths = [
        affinity.rotate(
            line,
            angle_deg,
            origin=(centre.x, centre.y),
            use_radians=False,
        )
        for line in swaths_rotated
    ]

    return swaths


def _order_boustrophedon(lines: list[LineString]) -> list[LineString]:
    """
    Order passes spatially, then alternate direction so the tractor works
    backwards and forwards across the field.
    """
    if not lines:
        return []

    # A robust ordering for straight, parallel lines is by centroid position
    # along the normal direction to the line family.
    first = lines[0]
    x1, y1 = first.coords[0]
    x2, y2 = first.coords[-1]

    dx = x2 - x1
    dy = y2 - y1
    norm = math.hypot(dx, dy)

    if norm < 1e-9:
        return lines

    # Unit normal.
    nx = -dy / norm
    ny = dx / norm

    ordered = sorted(
        lines,
        key=lambda line: line.centroid.x * nx + line.centroid.y * ny,
    )

    result = []

    for i, line in enumerate(ordered):
        coords = list(line.coords)

        # Give the first pass a deterministic left-to-right-ish direction.
        if i == 0:
            if coords[0][0] > coords[-1][0]:
                coords.reverse()
        else:
            previous_end = result[-1].coords[-1]

            d_start = math.dist(previous_end, coords[0])
            d_end = math.dist(previous_end, coords[-1])

            if d_end < d_start:
                coords.reverse()

        result.append(LineString(coords))

    return result


def _contour_alignment_error(
    angle_deg: float,
    dz_dx: np.ndarray,
    dz_dy: np.ndarray,
    useful_mask: np.ndarray,
) -> float:
    """
    Return mean angular error between the proposed STRAIGHT pass direction and
    the local equal-elevation direction.

    Gradient points uphill.
    A contour is perpendicular to gradient.

    Because a tractor line has no preferred forward/backward orientation,
    0° and 180° are equivalent.
    """
    theta = math.radians(angle_deg)

    # Candidate pass direction in image coordinates.
    ux = math.cos(theta)
    uy = math.sin(theta)

    gx = dz_dx[useful_mask]
    gy = dz_dy[useful_mask]

    gnorm = np.hypot(gx, gy)
    valid = gnorm > 1e-12

    if not np.any(valid):
        return 90.0

    gx = gx[valid] / gnorm[valid]
    gy = gy[valid] / gnorm[valid]

    # Contour direction = gradient rotated by 90 degrees.
    cx = -gy
    cy = gx

    dot = np.abs(cx * ux + cy * uy)
    dot = np.clip(dot, 0.0, 1.0)

    errors = np.degrees(np.arccos(dot))

    # Weight steeper cells more strongly because contour direction matters
    # more there and gradient direction is more reliable.
    weights = gnorm[valid]
    weights = weights / max(float(np.mean(weights)), 1e-12)

    return float(np.average(errors, weights=weights))


def _working_mask(
    working: Polygon,
    image_x: np.ndarray,
    image_y: np.ndarray,
    shape: tuple[int, int],
) -> np.ndarray:
    """
    Rasterise the working polygon onto the terrain grid.
    Uses point-in-polygon through OpenCV-compatible pixel conversion.
    """
    import cv2

    grid_h, grid_w = shape
    mask = np.zeros((grid_h, grid_w), dtype=np.uint8)

    x0, x1 = float(image_x[0]), float(image_x[-1])
    y0, y1 = float(image_y[0]), float(image_y[-1])

    sx = (grid_w - 1) / max(x1 - x0, 1e-9)
    sy = (grid_h - 1) / max(y1 - y0, 1e-9)

    polygons = [working] if working.geom_type == "Polygon" else list(working.geoms)

    for poly in polygons:
        exterior = np.asarray(poly.exterior.coords, dtype=float).copy()
        exterior[:, 0] = (exterior[:, 0] - x0) * sx
        exterior[:, 1] = (exterior[:, 1] - y0) * sy

        cv2.fillPoly(
            mask,
            [np.rint(exterior).astype(np.int32)],
            1,
        )

        for ring in poly.interiors:
            hole = np.asarray(ring.coords, dtype=float).copy()
            hole[:, 0] = (hole[:, 0] - x0) * sx
            hole[:, 1] = (hole[:, 1] - y0) * sy

            cv2.fillPoly(
                mask,
                [np.rint(hole).astype(np.int32)],
                0,
            )

    return mask.astype(bool)


def _coverage_percent(
    working: Polygon,
    swaths: list[LineString],
    implement_width_px: float,
) -> float:
    from shapely.ops import unary_union

    if working.area <= 0 or not swaths:
        return 0.0

    half_width = implement_width_px / 2.0

    strips = [
        line.buffer(
            half_width,
            cap_style=3,
            join_style=2,
        )
        for line in swaths
    ]

    covered = unary_union(strips).intersection(working)

    return float(100.0 * covered.area / working.area)


def _line_parts(geometry):
    if geometry.is_empty:
        return []

    if isinstance(geometry, LineString):
        return [geometry]

    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)

    if hasattr(geometry, "geoms"):
        return [
            part
            for part in geometry.geoms
            if isinstance(part, LineString)
        ]

    return []
