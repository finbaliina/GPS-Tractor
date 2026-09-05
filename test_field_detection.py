from pathlib import Path

import cv2
import numpy as np
import torch

from sam2.build_sam import build_sam2_hf
from sam2.sam2_image_predictor import SAM2ImagePredictor


# --------------------------------------------------
# SETTINGS
# --------------------------------------------------

IMAGE_PATH = Path("test_field.png")

# For a 1000 x 1000 image, this is the centre.
# Later we can calculate this from the tractor's GPS position.
FIELD_X = 500
FIELD_Y = 500

MODEL_ID = "facebook/sam2.1-hiera-tiny"

OUTPUT_PATH = Path("field_boundary.png")


# --------------------------------------------------
# LOAD IMAGE
# --------------------------------------------------

image_bgr = cv2.imread(str(IMAGE_PATH))

if image_bgr is None:
    raise FileNotFoundError(
        f"Could not open image: {IMAGE_PATH.resolve()}"
    )

image_rgb = cv2.cvtColor(
    image_bgr,
    cv2.COLOR_BGR2RGB,
)


# --------------------------------------------------
# LOAD SAM
# --------------------------------------------------

device = "cuda" if torch.cuda.is_available() else "cpu"

print(f"Using device: {device}")
print("Loading SAM 2.1 model...")

model = build_sam2_hf(
    MODEL_ID,
    device=device,
)

predictor = SAM2ImagePredictor(model)


# --------------------------------------------------
# RUN SEGMENTATION
# --------------------------------------------------

point_coords = np.array(
    [[FIELD_X, FIELD_Y]],
    dtype=np.float32,
)

# 1 = positive point:
# "the object/region I want contains this point"
point_labels = np.array(
    [1],
    dtype=np.int32,
)

print("Running segmentation...")

with torch.inference_mode():

    if device == "cuda":
        with torch.autocast(
            device_type="cuda",
            dtype=torch.float16,
        ):
            predictor.set_image(image_rgb)

            masks, scores, _ = predictor.predict(
                point_coords=point_coords,
                point_labels=point_labels,
                multimask_output=True,
            )

    else:
        predictor.set_image(image_rgb)

        masks, scores, _ = predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=True,
        )


# --------------------------------------------------
# CHOOSE BEST MASK
# --------------------------------------------------

best_index = int(np.argmax(scores))

best_mask = masks[best_index]

print(f"SAM score: {scores[best_index]:.3f}")


# --------------------------------------------------
# CONVERT MASK TO CONTOUR
# --------------------------------------------------

mask_uint8 = (
    best_mask.astype(np.uint8) * 255
)

contours, _ = cv2.findContours(
    mask_uint8,
    cv2.RETR_EXTERNAL,
    cv2.CHAIN_APPROX_SIMPLE,
)

if not contours:
    raise RuntimeError(
        "SAM produced a mask, but no contour could be extracted."
    )

largest_contour = max(
    contours,
    key=cv2.contourArea,
)


# --------------------------------------------------
# SIMPLIFY THE CONTOUR
# --------------------------------------------------

perimeter = cv2.arcLength(
    largest_contour,
    True,
)

epsilon = 0.002 * perimeter

polygon = cv2.approxPolyDP(
    largest_contour,
    epsilon,
    True,
)

print(f"Boundary points: {len(polygon)}")


# --------------------------------------------------
# DRAW RESULT
# --------------------------------------------------

result = image_bgr.copy()

# Red field boundary
cv2.polylines(
    result,
    [polygon],
    isClosed=True,
    color=(0, 0, 255),
    thickness=4,
)

# Green prompt point
cv2.circle(
    result,
    (FIELD_X, FIELD_Y),
    radius=8,
    color=(0, 255, 0),
    thickness=-1,
)


# --------------------------------------------------
# SAVE OUTPUT
# --------------------------------------------------

cv2.imwrite(
    str(OUTPUT_PATH),
    result,
)

print(f"Saved result to: {OUTPUT_PATH.resolve()}")