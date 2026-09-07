from dataclasses import dataclass
from pathlib import Path
import json
import os

import cv2
import numpy as np
import torch
from shapely.geometry import Polygon


@dataclass(frozen=True)
class FieldBoundary:
    polygon: Polygon
    score: float
    mask_path: Path
    overlay_path: Path


def detect_field(image_path: Path, output_dir: Path) -> FieldBoundary:
    """Use SAM to segment the field containing the centre of the image."""
    from sam2.build_sam import build_sam2_hf
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width = image.shape[:2]
    centre = np.array([[width / 2, height / 2]], dtype=np.float32)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_id = os.getenv("SAM_MODEL", "facebook/sam2.1-hiera-tiny")

    print(f"SAM: {model_id} on {device}")
    predictor = SAM2ImagePredictor(build_sam2_hf(model_id, device=device))
    predictor.set_image(rgb)

    with torch.inference_mode():
        if device == "cuda":
            with torch.autocast("cuda", dtype=torch.float16):
                masks, scores, _ = predictor.predict(
                    point_coords=centre,
                    point_labels=np.array([1]),
                    multimask_output=True,
                )
        else:
            masks, scores, _ = predictor.predict(
                point_coords=centre,
                point_labels=np.array([1]),
                multimask_output=True,
            )

    # Choose the largest suggested region that contains the centre point.
    cx = width // 2
    cy = height // 2

    possible = []

    for i, mask in enumerate(masks):
        if mask[cy, cx]:
            area = np.count_nonzero(mask)
            possible.append((area, i))

    if not possible:
        raise RuntimeError("SAM did not detect a region containing the GPS position.")

    _, index = max(possible)

    mask = masks[index].astype(np.uint8) * 255
    score = float(scores[index])

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise RuntimeError("SAM did not return a usable field boundary.")

    contour = max(contours, key=cv2.contourArea)
    simplify_fraction = float(os.getenv("BOUNDARY_SIMPLIFY", "0.01"))
    epsilon = simplify_fraction * cv2.arcLength(contour, True)
    contour = cv2.approxPolyDP(contour, epsilon, True)

    points = contour[:, 0, :].astype(float)
    polygon = Polygon(points)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda p: p.area)
    if polygon.is_empty:
        raise RuntimeError("SAM did not produce a usable field polygon.")

    mask_path = output_dir / "mask.png"
    overlay_path = output_dir / "field_boundary.png"
    json_path = output_dir / "field_boundary.json"

    cv2.imwrite(str(mask_path), mask)

    overlay = image.copy()
    cv2.polylines(overlay, [contour], True, (0, 0, 255), 4, cv2.LINE_AA)
    cv2.circle(overlay, (width // 2, height // 2), 7, (0, 255, 0), -1)
    cv2.imwrite(str(overlay_path), overlay)

    json_path.write_text(
        json.dumps(
            {
                "sam_score": score,
                "pixel_points": points.tolist(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"SAM score: {score:.3f}")
    print(f"Saved boundary: {overlay_path}")

    return FieldBoundary(polygon, score, mask_path, overlay_path)
