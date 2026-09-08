from __future__ import annotations

"""Discover field candidates inside the farmer's selected farm area.

The farm area is created once during setup by drawing one or more boxes on a
zoomed-out satellite image.  This module downloads detailed imagery for that
area, runs SAM2, deduplicates overlapping detections and stores a review queue.
"""

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any

import cv2
import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, Polygon, box, mapping, shape
from shapely.ops import transform

from farm_data import FARMS_DIR, _unique_dir, slugify, write_boundary_points_compatible
from farm_setup import fetch_mapbox_satellite


EARTH_CIRCUMFERENCE_M = 40075016.68557849
MAPBOX_TILE_SIZE = 512


@dataclass
class TileInfo:
    id: str
    centre_x: float
    centre_y: float
    centre_lon: float
    centre_lat: float
    zoom: float
    width: int
    height: int
    metres_per_pixel: float
    path: Path


def discover_fields(farm_id: str, force: bool = False) -> dict[str, Any]:
    farm_dir = FARMS_DIR / farm_id
    search_path = farm_dir / "farm_search_area.geojson"
    if not search_path.exists():
        raise FileNotFoundError("This farm has no saved search area. Run farm setup first.")

    candidate_root = farm_dir / "field_candidates"
    if force and candidate_root.exists():
        shutil.rmtree(candidate_root)
    candidate_root.mkdir(exist_ok=True)

    existing = list_candidates(farm_id)
    if existing and not force:
        return {"ok": True, "candidate_count": len(existing), "reused": True}

    search_geom_wgs = shape(json.loads(search_path.read_text(encoding="utf-8"))["geometry"])
    if search_geom_wgs.is_empty:
        raise RuntimeError("The saved farm search area is empty.")

    to_bng = Transformer.from_crs(4326, 27700, always_xy=True)
    to_wgs = Transformer.from_crs(27700, 4326, always_xy=True)
    search = transform(to_bng.transform, search_geom_wgs)

    width = int(os.getenv("FIELD_DISCOVERY_IMAGE_SIZE", "1000"))
    height = width
    target_mpp = float(os.getenv("FIELD_DISCOVERY_MPP", "0.8"))
    overlap = float(os.getenv("FIELD_DISCOVERY_TILE_OVERLAP", "0.12"))
    context_buffer_m = float(os.getenv("FIELD_DISCOVERY_CONTEXT_BUFFER_M", "120"))
    tile_span_m = width * target_mpp
    step_m = tile_span_m * (1.0 - overlap)
    max_tiles = int(os.getenv("FIELD_DISCOVERY_MAX_TILES", "180"))

    # A small imagery-only buffer helps SAM see the full boundary of fields that
    # lie on the edge of the farmer's rough box.  Prompt points remain inside the
    # actual selected area.
    tile_search = search.buffer(context_buffer_m)
    minx, miny, maxx, maxy = tile_search.bounds

    x_values = _centres_covering(minx, maxx, tile_span_m, step_m)
    y_values = _centres_covering(miny, maxy, tile_span_m, step_m)

    tiles: list[TileInfo] = []
    tile_dir = farm_dir / "discovery_tiles"
    if force and tile_dir.exists():
        shutil.rmtree(tile_dir)
    tile_dir.mkdir(exist_ok=True)

    for y in y_values:
        for x in x_values:
            footprint = box(
                x - tile_span_m / 2,
                y - tile_span_m / 2,
                x + tile_span_m / 2,
                y + tile_span_m / 2,
            )
            if not footprint.intersects(tile_search):
                continue
            if len(tiles) >= max_tiles:
                raise RuntimeError(
                    f"The selected area needs more than {max_tiles} detailed imagery tiles. "
                    "Draw tighter farm boxes, increase FIELD_DISCOVERY_MAX_TILES, or increase "
                    "FIELD_DISCOVERY_MPP slightly."
                )

            lon, lat = to_wgs.transform(float(x), float(y))
            zoom = _zoom_for_mpp(lat, target_mpp)
            mpp = _mpp(lat, zoom)
            tile_id = f"tile_{len(tiles) + 1:03d}"
            path = tile_dir / f"{tile_id}.png"
            if not path.exists():
                print(f"Downloading field discovery tile {len(tiles) + 1}...")
                fetch_mapbox_satellite(lat, lon, zoom, width, height, path)

            tiles.append(
                TileInfo(tile_id, x, y, lon, lat, zoom, width, height, mpp, path)
            )

    print(f"Running field detection across {len(tiles)} detailed imagery tiles...")
    raw = _run_sam_on_tiles(tiles, search)
    deduped = _dedupe(raw)
    _save_candidates(farm_id, deduped, candidate_root, to_wgs)

    summary = {
        "ok": True,
        "candidate_count": len(deduped),
        "tile_count": len(tiles),
        "reused": False,
    }
    (farm_dir / "field_discovery.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _centres_covering(minimum: float, maximum: float, span: float, step: float) -> np.ndarray:
    start = minimum + span / 2.0
    if maximum - minimum <= span:
        return np.array([(minimum + maximum) / 2.0])
    return np.arange(start, maximum + span / 2.0, step)


def _run_sam_on_tiles(tiles: list[TileInfo], search) -> list[dict[str, Any]]:
    import torch
    from sam2.build_sam import build_sam2_hf
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_id = os.getenv("SAM_MODEL", "facebook/sam2.1-hiera-tiny")
    predictor = SAM2ImagePredictor(build_sam2_hf(model_id, device=device))

    spacing_m = float(os.getenv("FIELD_DISCOVERY_PROMPT_SPACING_M", "120"))
    min_area_m2 = float(os.getenv("FIELD_MIN_AREA_HA", "0.35")) * 10000.0
    max_area_m2 = float(os.getenv("FIELD_MAX_AREA_HA", "250")) * 10000.0
    simplify_m = float(os.getenv("FIELD_DISCOVERY_SIMPLIFY_M", "2.0"))
    min_score = float(os.getenv("FIELD_DISCOVERY_MIN_SAM_SCORE", "0.60"))
    min_overlap = float(os.getenv("FIELD_DISCOVERY_MIN_AREA_OVERLAP", "0.35"))
    results: list[dict[str, Any]] = []

    for tile_index, tile in enumerate(tiles, start=1):
        print(f"SAM tile {tile_index}/{len(tiles)}: {tile.id}")
        image = cv2.imread(str(tile.path))
        if image is None:
            continue
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        predictor.set_image(rgb)
        prompt_px = max(40, int(round(spacing_m / tile.metres_per_pixel)))

        for py in range(prompt_px // 2, tile.height, prompt_px):
            for px in range(prompt_px // 2, tile.width, prompt_px):
                bx = tile.centre_x + (px - tile.width / 2) * tile.metres_per_pixel
                by = tile.centre_y - (py - tile.height / 2) * tile.metres_per_pixel
                if not search.covers(Point(bx, by)):
                    continue

                coords = np.array([[px, py]], dtype=np.float32)
                labels = np.array([1], dtype=np.int32)
                with torch.inference_mode():
                    if device == "cuda":
                        with torch.autocast("cuda", dtype=torch.float16):
                            masks, scores, _ = predictor.predict(
                                point_coords=coords,
                                point_labels=labels,
                                multimask_output=True,
                            )
                    else:
                        masks, scores, _ = predictor.predict(
                            point_coords=coords,
                            point_labels=labels,
                            multimask_output=True,
                        )

                choices = []
                for i, mask in enumerate(masks):
                    if mask[int(py), int(px)]:
                        choices.append((np.count_nonzero(mask), i))
                if not choices:
                    continue

                _, idx = max(choices)
                score = float(scores[idx])
                if score < min_score:
                    continue

                mask = masks[idx].astype(np.uint8)
                contours, _ = cv2.findContours(
                    mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                if not contours:
                    continue
                contour = max(contours, key=cv2.contourArea)
                eps = max(1.0, simplify_m / tile.metres_per_pixel)
                contour = cv2.approxPolyDP(contour, eps, True)
                pts = contour[:, 0, :].astype(float)
                if len(pts) < 3:
                    continue

                bng_pts = [
                    [
                        tile.centre_x + (x - tile.width / 2) * tile.metres_per_pixel,
                        tile.centre_y - (y - tile.height / 2) * tile.metres_per_pixel,
                    ]
                    for x, y in pts
                ]
                geom = Polygon(bng_pts)
                if not geom.is_valid:
                    geom = geom.buffer(0)
                if geom.is_empty or geom.geom_type not in {"Polygon", "MultiPolygon"}:
                    continue
                if geom.geom_type == "MultiPolygon":
                    geom = max(geom.geoms, key=lambda g: g.area)

                area = float(geom.area)
                if area < min_area_m2 or area > max_area_m2:
                    continue

                overlap_share = geom.intersection(search).area / max(area, 1.0)
                if overlap_share < min_overlap:
                    continue

                min_px = np.min(pts, axis=0)
                max_px = np.max(pts, axis=0)
                edge_margin_px = float(
                    min(min_px[0], min_px[1], tile.width - max_px[0], tile.height - max_px[1])
                )

                results.append({
                    "geometry_bng": geom,
                    "score": score,
                    "tile": tile,
                    "pixel_points": pts.tolist(),
                    "area_m2": area,
                    "edge_margin_px": edge_margin_px,
                })

    return results


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    threshold = float(os.getenv("FIELD_DEDUP_IOU", "0.55"))

    # Prefer detections that sit comfortably inside a tile; they are less likely
    # to be clipped at an imagery edge.  Score and area break ties.
    items = sorted(
        items,
        key=lambda x: (-x["edge_margin_px"], -x["score"], -x["area_m2"]),
    )
    kept: list[dict[str, Any]] = []
    for item in items:
        geom = item["geometry_bng"]
        duplicate = False
        for other in kept:
            inter = geom.intersection(other["geometry_bng"]).area
            union = geom.union(other["geometry_bng"]).area
            if union and inter / union >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(item)

    # Make the review order stable and roughly largest-first.
    return sorted(kept, key=lambda x: -x["area_m2"])


def _save_candidates(
    farm_id: str,
    items: list[dict[str, Any]],
    root: Path,
    to_wgs: Transformer,
) -> None:
    for i, item in enumerate(items, start=1):
        cid = f"candidate_{i:03d}"
        d = root / cid
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["tile"].path, d / "satellite.png")

        points = item["pixel_points"]
        write_boundary_points_compatible(d / "field_boundary.json", points)
        write_boundary_points_compatible(d / "field_boundary_detected.json", points)

        geom_wgs = transform(to_wgs.transform, item["geometry_bng"])
        meta = {
            "id": cid,
            "status": "pending",
            "suggested_name": f"Field {i}",
            "sam_score": item["score"],
            "area_ha": item["area_m2"] / 10000.0,
            "context": "inside selected farm area",
            "source_tile": item["tile"].id,
            "metres_per_pixel": item["tile"].metres_per_pixel,
            "map": {
                "centre_lat": item["tile"].centre_lat,
                "centre_lon": item["tile"].centre_lon,
                "zoom": item["tile"].zoom,
                "width": item["tile"].width,
                "height": item["tile"].height,
            },
            "geometry_wgs84": mapping(geom_wgs),
        }
        (d / "candidate.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )

        image = cv2.imread(str(d / "satellite.png"))
        contour = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(image, [contour], True, (0, 255, 0), 4, cv2.LINE_AA)
        cv2.imwrite(str(d / "overlay.png"), image)


def list_candidates(farm_id: str) -> list[dict[str, Any]]:
    root = FARMS_DIR / farm_id / "field_candidates"
    if not root.exists():
        return []

    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        path = d / "candidate.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            data["id"] = d.name
            out.append(data)
    return out


def set_candidate_status(farm_id: str, candidate_id: str, status: str) -> dict[str, Any]:
    path = FARMS_DIR / farm_id / "field_candidates" / candidate_id / "candidate.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = status
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def approve_candidate(farm_id: str, candidate_id: str, field_name: str) -> dict[str, str]:
    farm_dir = FARMS_DIR / farm_id
    source = farm_dir / "field_candidates" / candidate_id
    if not source.exists():
        raise FileNotFoundError(candidate_id)

    field_name = field_name.strip() or candidate_id.replace("_", " ").title()
    fields_dir = farm_dir / "fields"
    fields_dir.mkdir(exist_ok=True)
    target = _unique_dir(fields_dir, slugify(field_name))
    target.mkdir()

    for name in ("satellite.png", "field_boundary.json", "field_boundary_detected.json"):
        shutil.copy2(source / name, target / name)

    points = json.loads((source / "field_boundary.json").read_text(encoding="utf-8"))["points"]
    write_boundary_points_compatible(target / "field_boundary_approved.json", points)

    meta = json.loads((source / "candidate.json").read_text(encoding="utf-8"))
    map_info = meta["map"]
    (target / "metadata.json").write_text(
        json.dumps({
            "position": {
                "latitude": map_info["centre_lat"],
                "longitude": map_info["centre_lon"],
            },
            "zoom": map_info["zoom"],
            "width": map_info["width"],
            "height": map_info["height"],
            "image_settings": {
                "zoom": map_info["zoom"],
                "width": map_info["width"],
                "height": map_info["height"],
            },
            "metres_per_pixel": meta["metres_per_pixel"],
            "source": "farm_field_discovery",
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    (target / "field.json").write_text(
        json.dumps({
            "name": field_name,
            "boundary_source": "cv_setup",
            "boundary_locked": False,
            "route_needs_regeneration": True,
            "source_candidate": candidate_id,
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    set_candidate_status(farm_id, candidate_id, "accepted")
    return {"id": target.name, "name": field_name}


def _mpp(lat: float, zoom: float) -> float:
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (
        MAPBOX_TILE_SIZE * 2**zoom
    )


def _zoom_for_mpp(lat: float, wanted: float) -> float:
    return math.log2(
        EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat))
        / (MAPBOX_TILE_SIZE * wanted)
    )
