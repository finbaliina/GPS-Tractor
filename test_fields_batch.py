from pathlib import Path
import os

import cv2
import numpy as np
import torch
from dotenv import load_dotenv

from sam2.build_sam import build_sam2_hf
from sam2.sam2_image_predictor import SAM2ImagePredictor

from rtk_satellite.mapbox import (
    ImageSettings,
    download_satellite_image,
)


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")

if not MAPBOX_TOKEN:
    raise RuntimeError("MAPBOX_TOKEN is not set in .env")


MODEL_ID = "facebook/sam2.1-hiera-tiny"

OUTPUT_DIR = Path("field_tests")

IMAGE_SIZE = 1000
ZOOM = 14


# A deliberately mixed selection of UK agricultural areas.
#
# These are test coordinates rather than a definitive benchmark dataset.
# If one happens to fall on a road/building rather than inside a field,
# just replace that coordinate.
FIELDS = [
    {
        "name": "scottish_borders",
        "lat": 55.843089,
        "lon": -3.102406,
    },
    {
        "name": "east_lothian",
        "lat": 55.9440,
        "lon": -2.6900,
    },
    {
        "name": "aberdeenshire",
        "lat": 57.3300,
        "lon": -2.3500,
    },
    {
        "name": "north_yorkshire",
        "lat": 54.1500,
        "lon": -1.2500,
    },
    {
        "name": "lincolnshire",
        "lat": 53.0500,
        "lon": -0.2500,
    },
    {
        "name": "cambridgeshire",
        "lat": 52.3500,
        "lon": 0.0500,
    },
    {
        "name": "norfolk",
        "lat": 52.7000,
        "lon": 0.8500,
    },
    {
        "name": "herefordshire",
        "lat": 52.1000,
        "lon": -2.6500,
    },
    {
        "name": "mid_wales",
        "lat": 52.4000,
        "lon": -3.4000,
    },
    {
        "name": "cornwall",
        "lat": 50.4300,
        "lon": -4.8000,
    },
]


# ============================================================
# SAM SETUP
# ============================================================

device = "cuda" if torch.cuda.is_available() else "cpu"

print(f"Using device: {device}")
print("Loading SAM...")

model = build_sam2_hf(
    MODEL_ID,
    device=device,
)

predictor = SAM2ImagePredictor(model)

print("SAM loaded.")
print()


# ============================================================
# IMAGE SETTINGS
# ============================================================

settings = ImageSettings(
    zoom=ZOOM,
    width=IMAGE_SIZE,
    height=IMAGE_SIZE,
    marker=False,
)


# The requested GPS coordinate is exactly in the centre
# of our Mapbox image.
FIELD_X = IMAGE_SIZE // 2
FIELD_Y = IMAGE_SIZE // 2


# ============================================================
# PROCESS ONE FIELD
# ============================================================

def process_field(field):

    name = field["name"]
    latitude = field["lat"]
    longitude = field["lon"]

    print("=" * 60)
    print(f"Testing: {name}")
    print(f"{latitude}, {longitude}")

    field_dir = OUTPUT_DIR / name
    field_dir.mkdir(parents=True, exist_ok=True)

    image_path = field_dir / "satellite.png"
    mask_path = field_dir / "mask.png"
    result_path = field_dir / "boundary.png"

    # --------------------------------------------------------
    # DOWNLOAD SATELLITE IMAGE
    # --------------------------------------------------------

    print("Downloading satellite image...")

    download_satellite_image(
        latitude=latitude,
        longitude=longitude,
        token=MAPBOX_TOKEN,
        settings=settings,
        destination=image_path,
    )

    image_bgr = cv2.imread(str(image_path))

    if image_bgr is None:
        print("ERROR: Could not load downloaded image.")
        return

    image_rgb = cv2.cvtColor(
        image_bgr,
        cv2.COLOR_BGR2RGB,
    )

    # --------------------------------------------------------
    # SAM
    # --------------------------------------------------------

    point_coords = np.array(
        [[FIELD_X, FIELD_Y]],
        dtype=np.float32,
    )

    point_labels = np.array(
        [1],
        dtype=np.int32,
    )

    print("Running SAM...")

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

    # --------------------------------------------------------
    # BEST MASK
    # --------------------------------------------------------

    best_index = int(np.argmax(scores))

    mask = masks[best_index]

    score = float(scores[best_index])

    print(f"SAM score: {score:.3f}")

    mask_uint8 = (
        mask.astype(np.uint8) * 255
    )

    cv2.imwrite(
        str(mask_path),
        mask_uint8,
    )

    # --------------------------------------------------------
    # FIND OUTLINE
    # --------------------------------------------------------

    contours, _ = cv2.findContours(
        mask_uint8,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    if not contours:
        print("No contour found.")
        return

    largest_contour = max(
        contours,
        key=cv2.contourArea,
    )

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

    # --------------------------------------------------------
    # DRAW RESULT
    # --------------------------------------------------------

    result = image_bgr.copy()

    cv2.polylines(
        result,
        [polygon],
        isClosed=True,
        color=(0, 0, 255),
        thickness=4,
    )

    cv2.circle(
        result,
        (FIELD_X, FIELD_Y),
        radius=8,
        color=(0, 255, 0),
        thickness=-1,
    )

    cv2.imwrite(
        str(result_path),
        result,
    )

    area_pixels = cv2.contourArea(largest_contour)

    print(f"Polygon points: {len(polygon)}")
    print(f"Mask area: {area_pixels:,.0f} pixels")
    print(f"Saved: {result_path}")
    print()


# ============================================================
# RUN ALL FIELDS
# ============================================================

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

for field in FIELDS:

    try:

        process_field(field)

    except Exception as error:

        print(
            f"ERROR processing "
            f"{field['name']}: {error}"
        )


print("=" * 60)
print("Finished.")
print(f"Results are in: {OUTPUT_DIR.resolve()}")