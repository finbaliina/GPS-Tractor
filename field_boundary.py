from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Polygon

from geometry_utils import clean_polygon
from json_io import write_json
from sam_utils import create_image_predictor, predict_masks
from settings import settings


@dataclass(frozen=True)
class FieldBoundary:
    polygon: Polygon
    score: float
    mask_path: Path
    overlay_path: Path


def detect_field(image_path: Path, output_dir: Path) -> FieldBoundary:
    """Use SAM to segment the field containing the centre of the image."""
    source_image = cv2.imread(str(image_path))
    if source_image is None:
        raise FileNotFoundError(image_path)

    image_height_px, image_width_px = source_image.shape[:2]
    centre_x_px = image_width_px // 2
    centre_y_px = image_height_px // 2
    centre_prompt = np.array(
        [[image_width_px / 2.0, image_height_px / 2.0]],
        dtype=np.float32,
    )
    positive_prompt_label = np.array([1], dtype=np.int32)

    predictor, device_name = create_image_predictor()
    predictor.set_image(cv2.cvtColor(source_image, cv2.COLOR_BGR2RGB))
    candidate_masks, candidate_scores, _ = predict_masks(
        predictor,
        device_name,
        centre_prompt,
        positive_prompt_label,
        multiple_masks=True,
    )

    # For the single-position workflow, choose the largest SAM suggestion that
    # contains the known GPS/centre point. Whole-farm discovery uses a stricter
    # confidence/refinement strategy in field_discovery.py.
    centre_containing_masks: list[tuple[int, int]] = []
    for mask_index, candidate_mask in enumerate(candidate_masks):
        if candidate_mask[centre_y_px, centre_x_px]:
            mask_area_pixels = int(np.count_nonzero(candidate_mask))
            centre_containing_masks.append((mask_area_pixels, mask_index))

    if not centre_containing_masks:
        raise RuntimeError("SAM did not detect a region containing the GPS position.")

    _, selected_mask_index = max(centre_containing_masks)
    selected_mask = candidate_masks[selected_mask_index].astype(np.uint8) * 255
    selected_score = float(candidate_scores[selected_mask_index])

    detected_contours, _ = cv2.findContours(
        selected_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not detected_contours:
        raise RuntimeError("SAM did not return a usable field boundary.")

    largest_contour = max(detected_contours, key=cv2.contourArea)
    simplify_epsilon = (
        settings.computer_vision.single_field_simplify_fraction
        * cv2.arcLength(largest_contour, True)
    )
    simplified_contour = cv2.approxPolyDP(largest_contour, simplify_epsilon, True)

    raw_boundary_points = simplified_contour[:, 0, :].astype(float)
    try:
        field_polygon = clean_polygon(Polygon(raw_boundary_points))
    except ValueError as exc:
        raise RuntimeError("SAM did not produce a usable field polygon.") from exc

    boundary_points = np.asarray(field_polygon.exterior.coords[:-1], dtype=float)
    boundary_contour = np.rint(boundary_points).astype(np.int32).reshape((-1, 1, 2))

    mask_path = output_dir / "mask.png"
    overlay_path = output_dir / "field_boundary.png"
    boundary_json_path = output_dir / "field_boundary.json"

    cv2.imwrite(str(mask_path), selected_mask)

    visual_settings = settings.visualisation
    overlay_image = source_image.copy()
    cv2.polylines(
        overlay_image,
        [boundary_contour],
        True,
        visual_settings.detected_boundary_colour_bgr,
        visual_settings.detected_boundary_line_thickness_px,
        cv2.LINE_AA,
    )
    cv2.circle(
        overlay_image,
        (centre_x_px, centre_y_px),
        visual_settings.gps_marker_radius_px,
        visual_settings.gps_marker_colour_bgr,
        -1,
    )
    cv2.imwrite(str(overlay_path), overlay_image)

    write_json(
        boundary_json_path,
        {
            "sam_score": selected_score,
            "pixel_points": boundary_points.tolist(),
        },
    )

    print(f"SAM score: {selected_score:.3f}")
    print(f"Saved boundary: {overlay_path}")

    return FieldBoundary(
        polygon=field_polygon,
        score=selected_score,
        mask_path=mask_path,
        overlay_path=overlay_path,
    )
