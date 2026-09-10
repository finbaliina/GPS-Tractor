"""Shared SAM2 setup and inference helpers."""

from __future__ import annotations

from contextlib import nullcontext

import numpy as np

from settings import settings


def create_image_predictor():
    import torch
    from sam2.build_sam import build_sam2_hf
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    device_name = "cuda" if torch.cuda.is_available() else "cpu"
    model_id = settings.computer_vision.sam_model_id
    print(f"SAM: {model_id} on {device_name}")

    model = build_sam2_hf(model_id, device=device_name)
    return SAM2ImagePredictor(model), device_name


def predict_masks(
    predictor,
    device_name: str,
    point_coordinates: np.ndarray,
    point_labels: np.ndarray,
    *,
    bounding_box: np.ndarray | None = None,
    multiple_masks: bool = True,
):
    import torch

    autocast_context = (
        torch.autocast("cuda", dtype=torch.float16)
        if device_name == "cuda"
        else nullcontext()
    )

    with torch.inference_mode(), autocast_context:
        return predictor.predict(
            point_coords=point_coordinates,
            point_labels=point_labels,
            box=bounding_box,
            multimask_output=multiple_masks,
        )
