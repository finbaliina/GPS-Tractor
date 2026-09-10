"""Two-stage automatic field discovery for farmer-selected search areas.

Stage 1 downloads one overview satellite image for each selected search-area
polygon and uses SAM2 to find rough field candidates with full-area context.
Stage 2 downloads a dedicated high-resolution image around each rough candidate
and runs SAM2 again to produce the boundary shown to the farmer.

There is deliberately no imagery tiling or stitching here. If a selected area is
too large for useful discovery, the farmer can scan a smaller box separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

import cv2
import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import transform

from farm_data import FARMS_DIR, slugify, unique_directory, write_boundary_points_compatible
from geometry_utils import clean_polygon
from json_io import read_json, write_json
from mapbox import fetch_satellite_image, metres_per_pixel, zoom_for_metres_per_pixel
from sam_utils import create_image_predictor, predict_masks
from settings import settings

SQUARE_METRES_PER_HECTARE = 10_000.0
WGS84_EPSG = 4326
BRITISH_NATIONAL_GRID_EPSG = 27700


@dataclass(frozen=True)
class MapImage:
    image_id: str
    centre_easting_m: float
    centre_northing_m: float
    centre_longitude_deg: float
    centre_latitude_deg: float
    zoom: float
    width_px: int
    height_px: int
    metres_per_pixel: float
    image_path: Path


@dataclass(frozen=True)
class RoughCandidate:
    geometry_bng: Polygon
    sam_score: float
    source_image: MapImage
    area_m2: float


@dataclass(frozen=True)
class RefinedCandidate:
    discovery_geometry_bng: Polygon
    geometry_bng: Polygon
    sam_score: float
    refinement_image: MapImage
    pixel_points: list[list[float]]
    area_m2: float


def discover_fields(farm_id: str, force: bool = False, progress_callback=None) -> dict[str, Any]:
    """Find and refine fields in the farm's saved search area."""
    farm_directory = FARMS_DIR / farm_id
    _report_progress(progress_callback, 1, "Preparing field scan...")
    search_area_path = farm_directory / "farm_search_area.geojson"
    if not search_area_path.exists():
        raise FileNotFoundError("This farm has no saved search area. Run farm setup first.")

    candidate_directory = farm_directory / "field_candidates"
    if force:
        shutil.rmtree(candidate_directory, ignore_errors=True)
        shutil.rmtree(farm_directory / "discovery_overviews", ignore_errors=True)
    candidate_directory.mkdir(exist_ok=True)

    existing_candidates = list_candidates(farm_id)
    if existing_candidates and not force:
        return {"ok": True, "candidate_count": len(existing_candidates), "reused": True}

    search_geometry_wgs84 = shape(read_json(search_area_path)["geometry"])
    if search_geometry_wgs84.is_empty:
        raise RuntimeError("The saved farm search area is empty.")

    wgs84_to_bng = Transformer.from_crs(WGS84_EPSG, BRITISH_NATIONAL_GRID_EPSG, always_xy=True)
    bng_to_wgs84 = Transformer.from_crs(BRITISH_NATIONAL_GRID_EPSG, WGS84_EPSG, always_xy=True)
    search_geometry_bng = transform(wgs84_to_bng.transform, search_geometry_wgs84)

    search_parts = _polygon_parts(search_geometry_bng)
    _report_progress(progress_callback, 5, "Downloading overview imagery...")
    overview_images = [
        _build_overview_image(farm_directory, part, index, bng_to_wgs84)
        for index, part in enumerate(search_parts, start=1)
    ]

    _report_progress(progress_callback, 12, "Loading SAM2 model...")
    predictor, device_name = create_image_predictor()
    _report_progress(progress_callback, 18, "Scanning overview for rough field outlines...")
    rough_candidates: list[RoughCandidate] = []
    for index, (overview, search_part) in enumerate(zip(overview_images, search_parts), start=1):
        print(f"Rough field discovery {index}/{len(overview_images)}: {overview.image_id}")
        overview_start = 18 + (index - 1) * 22 / max(len(overview_images), 1)
        overview_end = 18 + index * 22 / max(len(overview_images), 1)
        rough_candidates.extend(
            _discover_in_overview(
                predictor, device_name, overview, search_part,
                progress_callback=progress_callback,
                progress_start=overview_start,
                progress_end=overview_end,
            )
        )
    rough_candidates = _deduplicate_rough_candidates(rough_candidates)

    print(f"Rough discovery found {len(rough_candidates)} candidate fields. Refining...")
    _report_progress(progress_callback, 40, f"Found {len(rough_candidates)} candidates. Refining field outlines...")
    refined_candidates: list[RefinedCandidate] = []
    for index, rough_candidate in enumerate(rough_candidates, start=1):
        print(f"Refining field {index}/{len(rough_candidates)}...")
        refine_percent = 40 + 52 * (index - 1) / max(len(rough_candidates), 1)
        _report_progress(progress_callback, refine_percent, f"Refining field {index} of {len(rough_candidates)}...")
        refined = _refine_candidate(
            predictor, device_name, rough_candidate, farm_directory, index, bng_to_wgs84
        )
        if refined is not None:
            refined_candidates.append(refined)

    _report_progress(progress_callback, 94, "Saving candidate fields...")
    _save_candidate_queue(refined_candidates, candidate_directory, bng_to_wgs84)
    summary = {
        "ok": True,
        "candidate_count": len(refined_candidates),
        "rough_candidate_count": len(rough_candidates),
        "overview_image_count": len(overview_images),
        "reused": False,
        "architecture": "whole_area_then_per_field_refinement",
    }
    write_json(farm_directory / "field_discovery.json", summary)
    _report_progress(progress_callback, 100, f"Field scan complete: {len(refined_candidates)} candidates ready to review.")
    return summary


def _report_progress(progress_callback, percent: float, message: str) -> None:
    if progress_callback is None:
        return
    progress_callback({
        "percent": max(0, min(100, int(round(percent)))),
        "message": message,
    })


def _polygon_parts(geometry) -> list[Polygon]:
    if geometry.geom_type == "Polygon":
        return [clean_polygon(geometry)]
    if geometry.geom_type == "MultiPolygon":
        return [clean_polygon(part) for part in geometry.geoms if not part.is_empty]
    cleaned = clean_polygon(geometry)
    return [cleaned]


def _build_overview_image(
    farm_directory: Path,
    search_polygon: Polygon,
    index: int,
    bng_to_wgs84: Transformer,
) -> MapImage:
    cfg = settings.field_discovery
    buffered = search_polygon.buffer(cfg.discovery_context_margin_m)
    min_e, min_n, max_e, max_n = buffered.bounds
    centre_e = (min_e + max_e) / 2.0
    centre_n = (min_n + max_n) / 2.0
    centre_lon, centre_lat = bng_to_wgs84.transform(centre_e, centre_n)

    required_mpp = max(
        (max_e - min_e) / cfg.discovery_image_width_px,
        (max_n - min_n) / cfg.discovery_image_height_px,
    )
    zoom = max(1.0, min(18.0, zoom_for_metres_per_pixel(centre_lat, required_mpp)))
    actual_mpp = metres_per_pixel(centre_lat, zoom)
    image_id = f"overview_{index:02d}"
    image_path = farm_directory / "discovery_overviews" / f"{image_id}.png"
    if not image_path.exists():
        fetch_satellite_image(
            latitude_deg=centre_lat,
            longitude_deg=centre_lon,
            zoom=zoom,
            width_px=cfg.discovery_image_width_px,
            height_px=cfg.discovery_image_height_px,
            destination=image_path,
        )
    return MapImage(image_id, centre_e, centre_n, centre_lon, centre_lat, zoom,
                    cfg.discovery_image_width_px, cfg.discovery_image_height_px,
                    actual_mpp, image_path)


def _discover_in_overview(
    predictor, device_name: str, overview: MapImage, search_polygon: Polygon,
    progress_callback=None, progress_start: float = 0.0, progress_end: float = 1.0,
) -> list[RoughCandidate]:
    image = cv2.imread(str(overview.image_path))
    if image is None:
        raise RuntimeError(f"Could not read discovery image {overview.image_path}")
    predictor.set_image(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    cfg = settings.field_discovery
    spacing_px = max(cfg.discovery_minimum_prompt_spacing_px,
                     int(round(cfg.discovery_prompt_spacing_m / overview.metres_per_pixel)))
    prompt_positions: list[tuple[int, int]] = []
    for y in range(spacing_px // 2, overview.height_px, spacing_px):
        for x in range(spacing_px // 2, overview.width_px, spacing_px):
            easting, northing = _pixel_to_bng(x, y, overview)
            if search_polygon.covers(Point(easting, northing)):
                prompt_positions.append((x, y))

    candidates: list[RoughCandidate] = []
    total_prompts = max(len(prompt_positions), 1)
    for prompt_number, (x, y) in enumerate(prompt_positions, start=1):
        candidate = _rough_candidate_at_prompt(predictor, device_name, overview, search_polygon, x, y)
        if candidate is not None:
            candidates.append(candidate)
        progress = progress_start + (progress_end - progress_start) * prompt_number / total_prompts
        _report_progress(
            progress_callback, progress,
            f"Scanning overview: prompt {prompt_number} of {len(prompt_positions)}...",
        )
    return candidates


def _rough_candidate_at_prompt(predictor, device_name: str, overview: MapImage,
                               search_polygon: Polygon, x: int, y: int) -> RoughCandidate | None:
    cfg = settings.field_discovery
    points = np.array([[x, y]], dtype=np.float32)
    labels = np.array([1], dtype=np.int32)
    masks, scores, _ = predict_masks(predictor, device_name, points, labels, multiple_masks=True)
    min_area = cfg.minimum_field_area_ha * SQUARE_METRES_PER_HECTARE * cfg.prefilter_minimum_area_ratio
    max_area = cfg.maximum_field_area_ha * SQUARE_METRES_PER_HECTARE * cfg.prefilter_maximum_area_ratio
    choices: list[tuple[float, Polygon]] = []
    for mask, score in zip(masks, scores):
        if not mask[y, x] or float(score) < cfg.discovery_minimum_sam_score:
            continue
        contour = _largest_mask_contour(mask)
        if contour is None:
            continue
        polygon = _contour_to_bng_polygon(contour, overview)
        if polygon is None or not min_area <= polygon.area <= max_area:
            continue
        overlap = polygon.intersection(search_polygon).area / max(polygon.area, 1.0)
        if overlap < cfg.minimum_search_overlap_fraction:
            continue
        choices.append((float(score), polygon))
    if not choices:
        return None

    # SAM commonly returns several valid interpretations for the same point.
    # The highest-score mask can sometimes encompass a whole block of adjoining
    # fields. Keep masks whose scores are close to the best result, then prefer
    # the smallest plausible one. This biases discovery toward individual fields
    # without accepting a clearly worse segmentation.
    score, polygon = _prefer_individual_field_mask(
        choices, cfg.discovery_mask_score_tolerance
    )
    return RoughCandidate(polygon, score, overview, float(polygon.area))



def _prefer_individual_field_mask(
    choices: list[tuple[float, Polygon]], score_tolerance: float
) -> tuple[float, Polygon]:
    """Prefer an individual-field mask when SAM returns nested alternatives.

    SAM's multimask output often contains a high-confidence broad region plus a
    slightly lower-confidence mask around the actual prompted field. We retain
    only masks close to the best confidence score, then take the smallest area.
    """
    best_score = max(score for score, _ in choices)
    competitive = [
        (score, polygon)
        for score, polygon in choices
        if score >= best_score - score_tolerance
    ]
    return min(competitive, key=lambda item: item[1].area)

def _deduplicate_rough_candidates(candidates: list[RoughCandidate]) -> list[RoughCandidate]:
    ordered = sorted(candidates, key=lambda c: (-c.sam_score, -c.area_m2))
    kept: list[RoughCandidate] = []
    threshold = settings.field_discovery.deduplication_iou_threshold
    for candidate in ordered:
        if any(_intersection_over_union(candidate.geometry_bng, other.geometry_bng) >= threshold for other in kept):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda c: -c.area_m2)


def _refine_candidate(predictor, device_name: str, rough: RoughCandidate,
                      farm_directory: Path, index: int,
                      bng_to_wgs84: Transformer,
                      margin_multiplier: float = 1.0) -> RefinedCandidate | None:
    cfg = settings.field_discovery
    min_e, min_n, max_e, max_n = rough.geometry_bng.bounds
    field_width = max_e - min_e
    field_height = max_n - min_n
    margin_m = max(cfg.refinement_minimum_margin_m,
                   max(field_width, field_height) * cfg.refinement_margin_fraction)
    margin_m *= max(1.0, float(margin_multiplier))
    min_e -= margin_m; min_n -= margin_m; max_e += margin_m; max_n += margin_m
    centre_e = (min_e + max_e) / 2.0
    centre_n = (min_n + max_n) / 2.0
    centre_lon, centre_lat = bng_to_wgs84.transform(centre_e, centre_n)
    required_mpp = max((max_e-min_e)/cfg.refinement_image_width_px,
                       (max_n-min_n)/cfg.refinement_image_height_px)
    target_mpp = max(required_mpp, cfg.refinement_target_metres_per_pixel)
    zoom = max(1.0, min(18.0, zoom_for_metres_per_pixel(centre_lat, target_mpp)))
    actual_mpp = metres_per_pixel(centre_lat, zoom)
    image_path = farm_directory / "field_refinement" / f"candidate_{index:03d}.png"
    fetch_satellite_image(centre_lat, centre_lon, zoom,
                          cfg.refinement_image_width_px, cfg.refinement_image_height_px, image_path)
    image_map = MapImage(f"refinement_{index:03d}", centre_e, centre_n, centre_lon, centre_lat,
                         zoom, cfg.refinement_image_width_px, cfg.refinement_image_height_px,
                         actual_mpp, image_path)
    image = cv2.imread(str(image_path))
    if image is None:
        return None
    predictor.set_image(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

    representative = rough.geometry_bng.representative_point()
    px, py = _bng_to_pixel(representative.x, representative.y, image_map)
    px = float(np.clip(px, 0, image_map.width_px - 1)); py = float(np.clip(py, 0, image_map.height_px - 1))
    rough_pixels = np.asarray([_bng_to_pixel(e, n, image_map) for e, n in rough.geometry_bng.exterior.coords[:-1]])
    box_left, box_top = np.min(rough_pixels, axis=0)
    box_right, box_bottom = np.max(rough_pixels, axis=0)
    pad_px = (cfg.refinement_box_padding_m * max(1.0, float(margin_multiplier))) / actual_mpp
    box_prompt = np.array([
        max(0.0, box_left-pad_px), max(0.0, box_top-pad_px),
        min(image_map.width_px-1.0, box_right+pad_px), min(image_map.height_px-1.0, box_bottom+pad_px)
    ], dtype=np.float32)
    masks, scores, _ = predict_masks(
        predictor, device_name,
        np.array([[px, py]], dtype=np.float32), np.array([1], dtype=np.int32),
        bounding_box=box_prompt, multiple_masks=True,
    )
    choices: list[tuple[float, Polygon]] = []
    min_area = cfg.minimum_field_area_ha * SQUARE_METRES_PER_HECTARE
    max_area = cfg.maximum_field_area_ha * SQUARE_METRES_PER_HECTARE
    for mask, score in zip(masks, scores):
        if not mask[int(round(py)), int(round(px))] or float(score) < cfg.refinement_minimum_sam_score:
            continue
        contour = _largest_mask_contour(mask)
        if contour is None:
            continue
        polygon = _contour_to_bng_polygon(contour, image_map)
        if polygon is None or not min_area <= polygon.area <= max_area:
            continue
        rough_overlap = polygon.intersection(rough.geometry_bng).area / max(rough.geometry_bng.area, 1.0)
        if rough_overlap < cfg.refinement_minimum_rough_overlap_fraction:
            continue
        choices.append((float(score), polygon))
    if not choices:
        # Keep the rough geometry rather than losing a plausible field entirely.
        refined_score, refined_polygon = rough.sam_score, rough.geometry_bng
    else:
        refined_score, refined_polygon = _prefer_individual_field_mask(
            choices, cfg.refinement_mask_score_tolerance
        )

    refined_polygon = _clean_refined_boundary(refined_polygon)
    refined_polygon = _simplify_for_editing(refined_polygon)
    pixel_points = [list(_bng_to_pixel(e, n, image_map)) for e, n in refined_polygon.exterior.coords[:-1]]
    return RefinedCandidate(rough.geometry_bng, refined_polygon, refined_score,
                            image_map, pixel_points, float(refined_polygon.area))


def _largest_mask_contour(mask: np.ndarray) -> np.ndarray | None:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(contours, key=cv2.contourArea) if contours else None


def _contour_to_bng_polygon(contour: np.ndarray, map_image: MapImage) -> Polygon | None:
    points = contour[:, 0, :].astype(float)
    if len(points) < 3:
        return None
    try:
        return clean_polygon(Polygon([_pixel_to_bng(x, y, map_image) for x, y in points]))
    except ValueError:
        return None


def _clean_refined_boundary(field_polygon: Polygon) -> Polygon:
    """Remove narrow SAM artefacts before reducing the boundary to editable vertices.

    A positive then negative buffer is a geometric closing operation. It fills
    narrow inward channels (the occasional deep SAM "fjord") without simply
    increasing the simplification tolerance for the whole field. Small interior
    holes are also discarded. All distances are in British National Grid metres.

    Cleaning is deliberately fail-safe: if a geometry operation produces an
    unusable polygon, the original repaired SAM polygon is returned.
    """
    cfg = settings.field_discovery

    try:
        original = clean_polygon(field_polygon)
        cleaned = original

        minimum_hole_area = cfg.refinement_minimum_hole_area_m2
        if cleaned.interiors and minimum_hole_area > 0.0:
            retained_holes = []
            for interior in cleaned.interiors:
                hole_polygon = Polygon(interior)
                if hole_polygon.area >= minimum_hole_area:
                    retained_holes.append(list(interior.coords))
            cleaned = clean_polygon(Polygon(cleaned.exterior.coords, retained_holes))

        clean_distance = cfg.refinement_boundary_clean_m
        if clean_distance > 0.0:
            # Mitre joins preserve the generally angular character of field corners.
            closed = cleaned.buffer(clean_distance, join_style=2).buffer(
                -clean_distance, join_style=2
            )
            cleaned = clean_polygon(closed)

        return cleaned
    except (ValueError, TypeError):
        return clean_polygon(field_polygon)


def _simplify_for_editing(field_polygon: Polygon) -> Polygon:
    cfg = settings.field_discovery
    tolerance = max(cfg.minimum_simplify_tolerance_m, cfg.simplify_tolerance_m)
    best = field_polygon
    for _ in range(cfg.simplify_maximum_iterations):
        try:
            simplified = clean_polygon(field_polygon.simplify(tolerance, preserve_topology=True))
        except ValueError:
            break
        vertices = len(simplified.exterior.coords) - 1
        if vertices < cfg.minimum_editor_vertices:
            break
        best = simplified
        if vertices <= cfg.maximum_editor_vertices:
            break
        tolerance *= cfg.simplify_tolerance_growth
    return best


def _save_candidate_queue(candidates: list[RefinedCandidate], candidate_directory: Path,
                          bng_to_wgs84: Transformer) -> None:
    visual = settings.visualisation
    for number, candidate in enumerate(candidates, start=1):
        candidate_id = f"candidate_{number:03d}"
        path = candidate_directory / candidate_id
        path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate.refinement_image.image_path, path / "satellite.png")
        write_boundary_points_compatible(path / "field_boundary.json", candidate.pixel_points)
        write_boundary_points_compatible(path / "field_boundary_detected.json", candidate.pixel_points)
        write_json(path / "discovery_boundary.json", {
            "coordinate_system": "EPSG:4326",
            "geometry": mapping(transform(bng_to_wgs84.transform, candidate.discovery_geometry_bng)),
            "purpose": "rough whole-area SAM discovery boundary",
        })
        geometry_wgs84 = transform(bng_to_wgs84.transform, candidate.geometry_bng)
        map_image = candidate.refinement_image
        write_json(path / "candidate.json", {
            "id": candidate_id, "status": "pending", "suggested_name": f"Field no {number}",
            "sam_score": candidate.sam_score,
            "area_ha": candidate.area_m2 / SQUARE_METRES_PER_HECTARE,
            "context": "refined from whole-area discovery",
            "source_overview": "whole-area discovery",
            "metres_per_pixel": map_image.metres_per_pixel,
            "vertex_count": len(candidate.pixel_points),
            "map": {"centre_lat": map_image.centre_latitude_deg, "centre_lon": map_image.centre_longitude_deg,
                    "zoom": map_image.zoom, "width": map_image.width_px, "height": map_image.height_px},
            "geometry_wgs84": mapping(geometry_wgs84),
        })
        review = cv2.imread(str(path / "satellite.png"))
        if review is not None:
            contour = np.rint(np.asarray(candidate.pixel_points)).astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(review, [contour], True, visual.candidate_boundary_colour_bgr,
                          visual.candidate_boundary_line_thickness_px, cv2.LINE_AA)
            cv2.imwrite(str(path / "overlay.png"), review)


def list_candidates(farm_id: str) -> list[dict[str, Any]]:
    directory = FARMS_DIR / farm_id / "field_candidates"
    if not directory.exists():
        return []
    result = []
    for path in sorted(p for p in directory.iterdir() if p.is_dir()):
        metadata = path / "candidate.json"
        if metadata.exists():
            result.append(read_json(metadata))
    return result


def set_candidate_status(farm_id: str, candidate_id: str, status: str) -> None:
    if status not in {"pending", "accepted", "rejected", "duplicate"}:
        raise ValueError("Unknown candidate status.")
    path = FARMS_DIR / farm_id / "field_candidates" / candidate_id / "candidate.json"
    metadata = read_json(path); metadata["status"] = status; write_json(path, metadata)


def approve_candidate(farm_id: str, candidate_id: str, field_name: str) -> dict[str, str]:
    farm_directory = FARMS_DIR / farm_id
    candidate_path = farm_directory / "field_candidates" / candidate_id
    if not candidate_path.exists():
        raise FileNotFoundError(candidate_id)
    candidate_metadata = read_json(candidate_path / "candidate.json")
    cleaned_name = field_name.strip() or candidate_metadata.get("suggested_name") or candidate_id.replace("_", " ").title()
    fields_directory = farm_directory / "fields"; fields_directory.mkdir(exist_ok=True)
    new_field_directory = unique_directory(fields_directory, slugify(cleaned_name)); new_field_directory.mkdir()
    for filename in ("satellite.png", "field_boundary.json", "field_boundary_detected.json", "discovery_boundary.json"):
        source = candidate_path / filename
        if source.exists():
            shutil.copy2(source, new_field_directory / filename)
    current_boundary = read_json(candidate_path / "field_boundary.json")["points"]
    write_boundary_points_compatible(new_field_directory / "field_boundary_approved.json", current_boundary, source="cv_setup")
    map_information = candidate_metadata["map"]
    write_json(new_field_directory / "metadata.json", {
        "position": {"latitude": map_information["centre_lat"], "longitude": map_information["centre_lon"]},
        "zoom": map_information["zoom"], "width": map_information["width"], "height": map_information["height"],
        "image_settings": {"zoom": map_information["zoom"], "width": map_information["width"], "height": map_information["height"]},
        "metres_per_pixel": candidate_metadata["metres_per_pixel"], "source": "farm_field_discovery",
    })
    write_json(new_field_directory / "field.json", {
        "name": cleaned_name, "boundary_source": "cv_setup", "boundary_locked": False,
        "route_needs_regeneration": True, "source_candidate": candidate_id,
    })
    set_candidate_status(farm_id, candidate_id, "accepted")
    return {"id": new_field_directory.name, "name": cleaned_name}


def _intersection_over_union(first, second) -> float:
    intersection = first.intersection(second).area
    union = first.union(second).area
    return float(intersection / union) if union else 0.0


def _pixel_to_bng(pixel_x: float, pixel_y: float, map_image: MapImage) -> tuple[float, float]:
    return (
        float(map_image.centre_easting_m + (pixel_x - map_image.width_px / 2.0) * map_image.metres_per_pixel),
        float(map_image.centre_northing_m - (pixel_y - map_image.height_px / 2.0) * map_image.metres_per_pixel),
    )


def _bng_to_pixel(easting_m: float, northing_m: float, map_image: MapImage) -> tuple[float, float]:
    return (
        float((easting_m - map_image.centre_easting_m) / map_image.metres_per_pixel + map_image.width_px / 2.0),
        float((map_image.centre_northing_m - northing_m) / map_image.metres_per_pixel + map_image.height_px / 2.0),
    )

# ---------------------------------------------------------------------------
# Experimental sequential discovery branch
# ---------------------------------------------------------------------------

def prepare_sequential_discovery(farm_id: str, force: bool = False, progress_callback=None) -> dict[str, Any]:
    """Build a rough candidate pool once, without refining every candidate.

    The pool is ranked by SAM confidence. Only one candidate is refined at a
    time later, after previous farmer decisions have been taken into account.
    """
    farm_directory = FARMS_DIR / farm_id
    state_directory = farm_directory / "sequential_discovery"
    pool_path = state_directory / "rough_pool.json"
    if force:
        shutil.rmtree(state_directory, ignore_errors=True)
    state_directory.mkdir(parents=True, exist_ok=True)

    if pool_path.exists() and not force:
        pool = read_json(pool_path)
        return {"ok": True, "candidate_count": len(pool.get("candidates", [])), "reused": True}

    search_area_path = farm_directory / "farm_search_area.geojson"
    if not search_area_path.exists():
        raise FileNotFoundError("This farm has no saved search area. Run farm setup first.")

    _report_progress(progress_callback, 2, "Preparing sequential field scan...")
    search_geometry_wgs84 = shape(read_json(search_area_path)["geometry"])
    wgs84_to_bng = Transformer.from_crs(WGS84_EPSG, BRITISH_NATIONAL_GRID_EPSG, always_xy=True)
    bng_to_wgs84 = Transformer.from_crs(BRITISH_NATIONAL_GRID_EPSG, WGS84_EPSG, always_xy=True)
    search_geometry_bng = transform(wgs84_to_bng.transform, search_geometry_wgs84)
    search_parts = _polygon_parts(search_geometry_bng)

    _report_progress(progress_callback, 8, "Downloading overview imagery...")
    overview_images = [
        _build_overview_image(farm_directory, part, index, bng_to_wgs84)
        for index, part in enumerate(search_parts, start=1)
    ]
    _report_progress(progress_callback, 15, "Loading SAM2 model...")
    predictor, device_name = create_image_predictor()

    rough_candidates: list[RoughCandidate] = []
    for index, (overview, search_part) in enumerate(zip(overview_images, search_parts), start=1):
        start = 20 + (index - 1) * 65 / max(len(overview_images), 1)
        end = 20 + index * 65 / max(len(overview_images), 1)
        rough_candidates.extend(_discover_in_overview(
            predictor, device_name, overview, search_part,
            progress_callback=progress_callback, progress_start=start, progress_end=end,
        ))

    # Keep only the light existing IoU deduplication here. The point of this
    # branch is to let farmer decisions suppress later candidates dynamically.
    rough_candidates = _deduplicate_rough_candidates(rough_candidates)
    rough_candidates = sorted(rough_candidates, key=lambda c: c.sam_score, reverse=True)

    serialised = []
    for number, candidate in enumerate(rough_candidates, start=1):
        geometry_wgs84 = transform(bng_to_wgs84.transform, candidate.geometry_bng)
        prompt_point = candidate.geometry_bng.representative_point()
        serialised.append({
            "id": f"rough_{number:04d}",
            "status": "unused",
            "sam_score": candidate.sam_score,
            "area_m2": candidate.area_m2,
            "geometry_bng": mapping(candidate.geometry_bng),
            "geometry_wgs84": mapping(geometry_wgs84),
            "source_image_id": candidate.source_image.image_id,
            "prompt_bng": [prompt_point.x, prompt_point.y],
        })

    write_json(pool_path, {
        "architecture": "experimental_sequential_ranked_discovery",
        "candidates": serialised,
    })
    write_json(state_directory / "decisions.json", {"accepted": [], "rejected": [], "duplicate": []})
    _report_progress(progress_callback, 100, f"Sequential scan ready: {len(serialised)} rough possibilities ranked.")
    return {"ok": True, "candidate_count": len(serialised), "reused": False}


def _sequential_paths(farm_id: str) -> tuple[Path, Path]:
    directory = FARMS_DIR / farm_id / "sequential_discovery"
    return directory / "rough_pool.json", directory / "current_candidate.json"


def get_next_sequential_candidate(
    farm_id: str, preferred_rough_id: str | None = None
) -> dict[str, Any] | None:
    """Return/refine the next candidate, or a farmer-selected map candidate.

    With no preferred ID, the highest-ranked unused candidate is returned. When
    ``preferred_rough_id`` is supplied (for example from the farm map), that
    specific unused candidate is prepared instead. An already-presented current
    candidate is put back into the queue so choosing a different field on the map
    never silently discards it.
    """
    pool_path, current_path = _sequential_paths(farm_id)
    if not pool_path.exists():
        raise FileNotFoundError("Sequential discovery has not been prepared for this farm.")

    pool = read_json(pool_path)
    candidates = pool.get("candidates", [])

    if current_path.exists():
        current = read_json(current_path)
        if current.get("status") == "pending":
            if not preferred_rough_id or current.get("rough_id") == preferred_rough_id:
                return current

            # The farmer clicked another pending field on the map. Put the
            # currently displayed proposal back into the unused queue.
            current_rough_id = current.get("rough_id")
            for item in candidates:
                if item.get("id") == current_rough_id and item.get("status") == "presented":
                    item["status"] = "unused"
                    break
            write_json(pool_path, pool)
            if current_path.exists():
                current_path.unlink()
            shutil.rmtree(FARMS_DIR / farm_id / "sequential_discovery" / "current", ignore_errors=True)

    accepted_polygons = _configured_field_exclusion_polygons(farm_id)
    # A farmer-marked duplicate also represents an already-accounted-for area.
    # Rejected candidates are deliberately NOT added here: rejecting one bad
    # segmentation must not suppress a genuine field underneath it.
    duplicate_polygons = [
        clean_polygon(shape(item["geometry_bng"]))
        for item in candidates
        if item.get("status") == "duplicate"
    ]
    exclusion_polygons = accepted_polygons + duplicate_polygons

    chosen = None
    ordered_candidates = candidates
    if preferred_rough_id:
        selected = [item for item in candidates if item.get("id") == preferred_rough_id]
        if not selected:
            raise KeyError(f"Unknown field suggestion: {preferred_rough_id}")
        ordered_candidates = selected

    for item in ordered_candidates:
        if item.get("status", "unused") != "unused":
            continue
        polygon = shape(item["geometry_bng"])
        if any(_overlap_fraction(polygon, excluded) >= 0.55 for excluded in exclusion_polygons):
            item["status"] = "suppressed_by_accepted_field"
            continue
        chosen = item
        break
    write_json(pool_path, pool)
    if chosen is None:
        if current_path.exists():
            current_path.unlink()
        return None

    refined = _refine_sequential_rough_candidate(farm_id, chosen)
    chosen["status"] = "presented"
    for item in candidates:
        if item["id"] == chosen["id"]:
            item["status"] = "presented"
            break
    write_json(pool_path, pool)
    write_json(current_path, refined)
    return refined


def _configured_field_exclusion_polygons(farm_id: str) -> list[Polygon]:
    polygons: list[Polygon] = []
    fields_directory = FARMS_DIR / farm_id / "fields"
    if not fields_directory.exists():
        return polygons
    wgs84_to_bng = Transformer.from_crs(WGS84_EPSG, BRITISH_NATIONAL_GRID_EPSG, always_xy=True)
    for directory in fields_directory.iterdir():
        if not directory.is_dir():
            continue
        discovery_path = directory / "discovery_boundary.json"
        if not discovery_path.exists():
            continue
        try:
            geometry = shape(read_json(discovery_path)["geometry"])
            polygons.append(clean_polygon(transform(wgs84_to_bng.transform, geometry)))
        except Exception:
            continue
    return polygons


def _overlap_fraction(candidate: Polygon, accepted: Polygon) -> float:
    return candidate.intersection(accepted).area / max(candidate.area, 1.0)


def _refine_sequential_rough_candidate(
    farm_id: str, item: dict[str, Any], margin_multiplier: float = 1.0,
    sequence_number: int | None = None,
) -> dict[str, Any]:
    farm_directory = FARMS_DIR / farm_id
    bng_to_wgs84 = Transformer.from_crs(BRITISH_NATIONAL_GRID_EPSG, WGS84_EPSG, always_xy=True)
    overview_id = item.get("source_image_id", "overview_01")
    overview_index = int(overview_id.split("_")[-1]) if "_" in overview_id else 1

    search_geo = shape(read_json(farm_directory / "farm_search_area.geojson")["geometry"])
    wgs84_to_bng = Transformer.from_crs(WGS84_EPSG, BRITISH_NATIONAL_GRID_EPSG, always_xy=True)
    parts = _polygon_parts(transform(wgs84_to_bng.transform, search_geo))
    source_part = parts[min(max(overview_index - 1, 0), len(parts) - 1)]
    overview = _build_overview_image(farm_directory, source_part, overview_index, bng_to_wgs84)
    rough = RoughCandidate(
        geometry_bng=clean_polygon(shape(item["geometry_bng"])),
        sam_score=float(item["sam_score"]),
        source_image=overview,
        area_m2=float(item["area_m2"]),
    )

    predictor, device_name = create_image_predictor()
    if sequence_number is None:
        sequence_number = _next_sequential_number(farm_directory)
    refined = _refine_candidate(
        predictor, device_name, rough, farm_directory, sequence_number, bng_to_wgs84,
        margin_multiplier=margin_multiplier,
    )
    if refined is None:
        raise RuntimeError("SAM could not refine the selected sequential candidate.")

    candidate_directory = farm_directory / "sequential_discovery" / "current"
    shutil.rmtree(candidate_directory, ignore_errors=True)
    candidate_directory.mkdir(parents=True, exist_ok=True)
    _save_one_sequential_candidate(
        refined, candidate_directory, bng_to_wgs84, item["id"], sequence_number,
        margin_multiplier=margin_multiplier,
    )
    metadata = read_json(candidate_directory / "candidate.json")
    metadata["status"] = "pending"
    write_json(candidate_directory / "candidate.json", metadata)
    return metadata


def _next_sequential_number(farm_directory: Path) -> int:
    state_path = farm_directory / "sequential_discovery" / "counter.json"
    current = read_json(state_path).get("value", 0) if state_path.exists() else 0
    current += 1
    write_json(state_path, {"value": current})
    return current


def _save_one_sequential_candidate(candidate: RefinedCandidate, path: Path,
                                   bng_to_wgs84: Transformer, rough_id: str,
                                   number: int, margin_multiplier: float = 1.0) -> None:
    visual = settings.visualisation
    shutil.copy2(candidate.refinement_image.image_path, path / "satellite.png")
    write_boundary_points_compatible(path / "field_boundary.json", candidate.pixel_points)
    write_boundary_points_compatible(path / "field_boundary_detected.json", candidate.pixel_points)
    write_json(path / "discovery_boundary.json", {
        "coordinate_system": "EPSG:4326",
        "geometry": mapping(transform(bng_to_wgs84.transform, candidate.discovery_geometry_bng)),
        "purpose": "rough sequential SAM discovery boundary",
    })
    geometry_wgs84 = transform(bng_to_wgs84.transform, candidate.geometry_bng)
    map_image = candidate.refinement_image
    write_json(path / "candidate.json", {
        "id": "current", "rough_id": rough_id, "status": "pending",
        "suggested_name": f"Field no {number}", "sam_score": candidate.sam_score,
        "area_ha": candidate.area_m2 / SQUARE_METRES_PER_HECTARE,
        "metres_per_pixel": map_image.metres_per_pixel,
        "vertex_count": len(candidate.pixel_points),
        "map": {"centre_lat": map_image.centre_latitude_deg, "centre_lon": map_image.centre_longitude_deg,
                "zoom": map_image.zoom, "width": map_image.width_px, "height": map_image.height_px},
        "geometry_wgs84": mapping(geometry_wgs84),
        "refinement_margin_multiplier": float(margin_multiplier),
        "sequence_number": int(number),
    })
    review = cv2.imread(str(path / "satellite.png"))
    if review is not None:
        contour = np.rint(np.asarray(candidate.pixel_points)).astype(np.int32).reshape((-1, 1, 2))
        cv2.polylines(review, [contour], True, visual.candidate_boundary_colour_bgr,
                      visual.candidate_boundary_line_thickness_px, cv2.LINE_AA)
        cv2.imwrite(str(path / "overlay.png"), review)


def rescan_current_sequential_candidate(farm_id: str) -> dict[str, Any]:
    """Re-refine the currently presented field with a wider satellite view."""
    farm_directory = FARMS_DIR / farm_id
    pool_path, current_path = _sequential_paths(farm_id)
    if not pool_path.exists() or not current_path.exists():
        raise FileNotFoundError("There is no field waiting to be reviewed.")

    current = read_json(current_path)
    rough_id = current.get("rough_id")
    pool = read_json(pool_path)
    rough_item = next((item for item in pool.get("candidates", []) if item.get("id") == rough_id), None)
    if rough_item is None:
        raise KeyError(f"Could not find field suggestion {rough_id!r}.")

    old_candidate_path = farm_directory / "sequential_discovery" / "current" / "candidate.json"
    old_metadata = read_json(old_candidate_path) if old_candidate_path.exists() else current
    old_multiplier = float(old_metadata.get("refinement_margin_multiplier", 1.0))
    next_multiplier = min(old_multiplier * 1.75, 8.0)
    sequence_number = int(old_metadata.get("sequence_number", 1))

    refreshed = _refine_sequential_rough_candidate(
        farm_id, rough_item, margin_multiplier=next_multiplier, sequence_number=sequence_number
    )
    write_json(current_path, refreshed)
    return refreshed


def set_sequential_candidate_status(farm_id: str, status: str) -> None:
    if status not in {"rejected", "duplicate"}:
        raise ValueError("Sequential candidate status must be rejected or duplicate.")
    pool_path, current_path = _sequential_paths(farm_id)
    current = read_json(current_path)
    rough_id = current["rough_id"]
    pool = read_json(pool_path)
    for item in pool.get("candidates", []):
        if item["id"] == rough_id:
            item["status"] = status
            break
    write_json(pool_path, pool)
    if current_path.exists():
        current_path.unlink()
    shutil.rmtree(FARMS_DIR / farm_id / "sequential_discovery" / "current", ignore_errors=True)


def approve_sequential_candidate(farm_id: str, field_name: str) -> dict[str, str]:
    farm_directory = FARMS_DIR / farm_id
    pool_path, current_path = _sequential_paths(farm_id)
    current = read_json(current_path)
    candidate_path = farm_directory / "sequential_discovery" / "current"
    metadata = read_json(candidate_path / "candidate.json")
    cleaned_name = field_name.strip() or metadata.get("suggested_name", "Field")
    fields_directory = farm_directory / "fields"; fields_directory.mkdir(exist_ok=True)
    new_field_directory = unique_directory(fields_directory, slugify(cleaned_name)); new_field_directory.mkdir()
    for filename in ("satellite.png", "field_boundary.json", "field_boundary_detected.json", "discovery_boundary.json"):
        source = candidate_path / filename
        if source.exists():
            shutil.copy2(source, new_field_directory / filename)
    current_boundary = read_json(candidate_path / "field_boundary.json")["points"]
    write_boundary_points_compatible(new_field_directory / "field_boundary_approved.json", current_boundary, source="cv_setup")
    map_information = metadata["map"]
    write_json(new_field_directory / "metadata.json", {
        "position": {"latitude": map_information["centre_lat"], "longitude": map_information["centre_lon"]},
        "zoom": map_information["zoom"], "width": map_information["width"], "height": map_information["height"],
        "image_settings": {"zoom": map_information["zoom"], "width": map_information["width"], "height": map_information["height"]},
        "name": cleaned_name, "source": "sequential_field_discovery",
    })
    field_id = new_field_directory.name
    write_json(new_field_directory / "field.json", {
        "name": cleaned_name, "boundary_source": "cv_setup", "boundary_locked": False,
        "route_needs_regeneration": True, "source_candidate": current["rough_id"],
        "source": "sequential_field_discovery",
    })

    pool = read_json(pool_path)
    for item in pool.get("candidates", []):
        if item["id"] == current["rough_id"]:
            item["status"] = "accepted"
            break
    write_json(pool_path, pool)
    if current_path.exists():
        current_path.unlink()
    shutil.rmtree(candidate_path, ignore_errors=True)
    return {"id": field_id, "name": cleaned_name}


def sequential_discovery_stats(farm_id: str) -> dict[str, int]:
    pool_path, _ = _sequential_paths(farm_id)
    if not pool_path.exists():
        return {"total": 0, "unused": 0, "accepted": 0, "rejected": 0, "duplicate": 0, "suppressed": 0}
    candidates = read_json(pool_path).get("candidates", [])
    return {
        "total": len(candidates),
        "unused": sum(c.get("status", "unused") == "unused" for c in candidates),
        "accepted": sum(c.get("status") == "accepted" for c in candidates),
        "rejected": sum(c.get("status") == "rejected" for c in candidates),
        "duplicate": sum(c.get("status") == "duplicate" for c in candidates),
        "suppressed": sum(c.get("status") == "suppressed_by_accepted_field" for c in candidates),
    }
