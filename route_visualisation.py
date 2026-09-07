from pathlib import Path

import cv2
import numpy as np


def draw_route(
    image_path: Path,
    field,
    plan,
    output_path: Path,
):
    """Draw field, working polygon and straight/contour route over the image."""
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)

    _geometry_outline(image, field, (0, 255, 0), 3)
    _geometry_outline(image, plan.working_polygon, (0, 215, 255), 2)

    for i, swath in enumerate(plan.swaths):
        points = _line(swath)
        cv2.polylines(image, [points], False, (255, 0, 0), 2, cv2.LINE_AA)

        start = tuple(np.rint(swath.coords[0]).astype(int))
        cv2.circle(image, start, 3, (255, 255, 255), -1)

        if i == 0 or (i + 1) % 5 == 0:
            cv2.putText(
                image,
                str(i + 1),
                (start[0] + 4, start[1] - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (255, 255, 255),
                1,
            )

    # These are still route-order indicators only.
    # They are NOT yet steering-constrained tractor turns.
    for current, nxt in zip(plan.swaths, plan.swaths[1:]):
        a = tuple(np.rint(current.coords[-1]).astype(int))
        b = tuple(np.rint(nxt.coords[0]).astype(int))
        cv2.line(image, a, b, (255, 255, 255), 1, cv2.LINE_AA)

    cv2.imwrite(str(output_path), image)
    print(f"Saved route overlay: {output_path}")


def _geometry_outline(image, geometry, colour, thickness):
    if geometry.geom_type == "Polygon":
        polygons = [geometry]
    elif geometry.geom_type == "MultiPolygon":
        polygons = list(geometry.geoms)
    else:
        return

    for polygon in polygons:
        points = np.rint(np.asarray(polygon.exterior.coords)).astype(np.int32)
        cv2.polylines(image, [points], True, colour, thickness, cv2.LINE_AA)


def _line(line):
    return np.rint(np.asarray(line.coords)).astype(np.int32)
