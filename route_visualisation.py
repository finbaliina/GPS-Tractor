from pathlib import Path

import cv2
import numpy as np

from settings import settings


visual_settings = settings.visualisation


def draw_route(image_path: Path, field_polygon, route_plan, output_path: Path) -> None:
    """Draw a planned route over the source satellite image."""
    satellite_image = cv2.imread(str(image_path))
    if satellite_image is None:
        raise FileNotFoundError(image_path)

    _draw_geometry_outline(
        satellite_image,
        field_polygon,
        visual_settings.route_field_colour_bgr,
        visual_settings.route_field_line_thickness_px,
    )
    _draw_geometry_outline(
        satellite_image,
        route_plan.working_polygon,
        visual_settings.route_working_area_colour_bgr,
        visual_settings.route_working_area_line_thickness_px,
    )

    for pass_index, swath_line in enumerate(route_plan.swaths):
        swath_pixels = _line_pixels(swath_line)
        cv2.polylines(
            satellite_image,
            [swath_pixels],
            False,
            visual_settings.route_swath_colour_bgr,
            visual_settings.route_swath_line_thickness_px,
            cv2.LINE_AA,
        )

        pass_start = _coordinate_to_pixel(swath_line.coords[0])
        cv2.circle(
            satellite_image,
            pass_start,
            visual_settings.route_start_marker_radius_px,
            visual_settings.route_start_marker_colour_bgr,
            -1,
        )

        pass_number = pass_index + 1
        if _should_draw_pass_number(pass_number):
            label_position = (
                pass_start[0] + visual_settings.route_label_offset_x_px,
                pass_start[1] + visual_settings.route_label_offset_y_px,
            )
            cv2.putText(
                satellite_image,
                str(pass_number),
                label_position,
                cv2.FONT_HERSHEY_SIMPLEX,
                visual_settings.route_label_font_scale,
                visual_settings.route_start_marker_colour_bgr,
                visual_settings.route_label_line_thickness_px,
                cv2.LINE_AA,
            )

    _draw_route_order_connectors(satellite_image, route_plan.swaths)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), satellite_image):
        raise RuntimeError(f"Could not save route overlay to {output_path}")

    print(f"Saved route overlay: {output_path}")


def _should_draw_pass_number(pass_number: int) -> bool:
    """Label the first pass and then every configured Nth pass."""
    label_interval = visual_settings.route_label_every_n_passes
    return pass_number == 1 or pass_number % label_interval == 0


def _draw_route_order_connectors(image, swath_lines) -> None:
    """Draw route-order indicators between passes.

    These connectors are visual aids only. They are not yet steering-constrained
    tractor turns.
    """
    for current_swath, next_swath in zip(swath_lines, swath_lines[1:]):
        current_end = _coordinate_to_pixel(current_swath.coords[-1])
        next_start = _coordinate_to_pixel(next_swath.coords[0])
        cv2.line(
            image,
            current_end,
            next_start,
            visual_settings.route_connector_colour_bgr,
            visual_settings.route_connector_line_thickness_px,
            cv2.LINE_AA,
        )


def _draw_geometry_outline(image, geometry, colour_bgr, line_thickness_px: int) -> None:
    polygons = _polygon_parts(geometry)
    for polygon in polygons:
        exterior_pixels = np.rint(np.asarray(polygon.exterior.coords)).astype(np.int32)
        cv2.polylines(
            image,
            [exterior_pixels],
            True,
            colour_bgr,
            line_thickness_px,
            cv2.LINE_AA,
        )


def _polygon_parts(geometry):
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return []


def _line_pixels(line_geometry) -> np.ndarray:
    return np.rint(np.asarray(line_geometry.coords)).astype(np.int32)


def _coordinate_to_pixel(coordinate) -> tuple[int, int]:
    rounded = np.rint(coordinate).astype(int)
    return int(rounded[0]), int(rounded[1])
