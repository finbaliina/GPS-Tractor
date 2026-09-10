"""Typed application settings loaded from .env.

All user-tuneable values live in .env.  This module is deliberately the only
place that converts environment strings into Python values, so the rest of the
project can use descriptive, typed settings rather than scattered os.getenv()
calls and duplicated defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parent


class SettingsError(RuntimeError):
    """Raised when a required configuration value is missing or invalid."""


def _load_raw_settings() -> dict[str, str]:
    """Load template defaults, then .env overrides, then real process env values."""
    values: dict[str, str] = {}

    for dotenv_path in (PROJECT_ROOT / ".env.example", PROJECT_ROOT / ".env"):
        if not dotenv_path.exists():
            continue
        for key, value in dotenv_values(dotenv_path).items():
            if value is not None:
                values[key] = value

    # Explicit process environment variables win over both files.
    values.update(os.environ)
    return values


_RAW_SETTINGS = _load_raw_settings()


def _required_text(name: str) -> str:
    value = _RAW_SETTINGS.get(name, "").strip()
    if not value:
        raise SettingsError(f"{name} is missing. Add it to .env.")
    return value


def _optional_text(name: str) -> str:
    return _RAW_SETTINGS.get(name, "").strip()


def _integer(name: str) -> int:
    raw_value = _required_text(name)
    try:
        return int(raw_value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, not {raw_value!r}.") from exc


def _number(name: str) -> float:
    raw_value = _required_text(name)
    try:
        return float(raw_value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number, not {raw_value!r}.") from exc


def _boolean(name: str) -> bool:
    raw_value = _required_text(name).lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(
        f"{name} must be true/false, yes/no, on/off, or 1/0; got {raw_value!r}."
    )


def _bgr_colour(name: str) -> tuple[int, int, int]:
    raw_value = _required_text(name)
    pieces = [piece.strip() for piece in raw_value.split(",")]
    if len(pieces) != 3:
        raise SettingsError(f"{name} must be three comma-separated B,G,R values.")

    try:
        channels = tuple(int(piece) for piece in pieces)
    except ValueError as exc:
        raise SettingsError(f"{name} contains a non-integer colour channel.") from exc

    if any(channel < 0 or channel > 255 for channel in channels):
        raise SettingsError(f"{name} colour channels must each be between 0 and 255.")

    return channels  # type: ignore[return-value]


def _fraction(name: str, *, upper: float = 1.0) -> float:
    value = _number(name)
    if not 0.0 <= value <= upper:
        raise SettingsError(f"{name} must be between 0 and {upper}.")
    return value


def _positive_number(name: str) -> float:
    value = _number(name)
    if value <= 0:
        raise SettingsError(f"{name} must be greater than zero.")
    return value


def _nonnegative_number(name: str) -> float:
    value = _number(name)
    if value < 0:
        raise SettingsError(f"{name} cannot be negative.")
    return value


def _positive_integer(name: str) -> int:
    value = _integer(name)
    if value <= 0:
        raise SettingsError(f"{name} must be greater than zero.")
    return value


@dataclass(frozen=True)
class MapboxSettings:
    token: str
    satellite_style: str
    request_timeout_s: float
    geocode_timeout_s: float
    geocode_country_code: str
    capture_zoom: float
    capture_width_px: int
    capture_height_px: int


@dataclass(frozen=True)
class GpsSettings:
    mode: str
    mock_latitude: float
    mock_longitude: float
    serial_port: str
    baud_rate: int
    stable_samples_required: int
    fix_timeout_s: float
    serial_read_timeout_s: float
    allow_float_rtk: bool


@dataclass(frozen=True)
class ImageReviewSettings:
    enabled: bool
    preview_width_px: int
    preview_height_px: int


@dataclass(frozen=True)
class ComputerVisionSettings:
    sam_model_id: str
    single_field_simplify_fraction: float


@dataclass(frozen=True)
class FarmSetupSettings:
    overview_radius_m: float
    overview_width_px: int
    overview_height_px: int
    minimum_selection_box_px: float


@dataclass(frozen=True)
class FieldDiscoverySettings:
    discovery_image_width_px: int
    discovery_image_height_px: int
    discovery_context_margin_m: float
    discovery_prompt_spacing_m: float
    discovery_minimum_prompt_spacing_px: int
    discovery_minimum_sam_score: float
    refinement_image_width_px: int
    refinement_image_height_px: int
    refinement_target_metres_per_pixel: float
    refinement_margin_fraction: float
    refinement_minimum_margin_m: float
    refinement_box_padding_m: float
    refinement_minimum_sam_score: float
    refinement_minimum_rough_overlap_fraction: float
    minimum_field_area_ha: float
    maximum_field_area_ha: float
    prefilter_minimum_area_ratio: float
    prefilter_maximum_area_ratio: float
    minimum_search_overlap_fraction: float
    deduplication_iou_threshold: float
    simplify_tolerance_m: float
    maximum_editor_vertices: int
    minimum_editor_vertices: int
    minimum_simplify_tolerance_m: float
    simplify_maximum_iterations: int
    simplify_tolerance_growth: float


@dataclass(frozen=True)
class RouteSettings:
    tractor_profile: str
    implement_width_m: float
    headland_width_m: float
    pass_overlap_fraction: float
    straight_angle_step_deg: float
    working_speed_mps: float
    turning_speed_mps: float
    fixed_turn_time_s: float


@dataclass(frozen=True)
class TerrainSettings:
    auto_fetch: bool
    terrain_tile_zoom: int
    grid_long_side_px: int
    contour_interval_m: float
    contour_slope_threshold_deg: float
    request_timeout_s: float
    overlay_dpi: int
    overlay_contour_line_width: float
    overlay_label_font_size: float
    overlay_boundary_line_width: float


@dataclass(frozen=True)
class ContourRouteSettings:
    angle_step_deg: float
    alignment_weight: float
    minimum_direction_slope_deg: float
    extra_overlap_fraction: float


@dataclass(frozen=True)
class VisualisationSettings:
    detected_boundary_colour_bgr: tuple[int, int, int]
    detected_boundary_line_thickness_px: int
    gps_marker_colour_bgr: tuple[int, int, int]
    gps_marker_radius_px: int
    candidate_boundary_colour_bgr: tuple[int, int, int]
    candidate_boundary_line_thickness_px: int
    route_field_colour_bgr: tuple[int, int, int]
    route_field_line_thickness_px: int
    route_working_area_colour_bgr: tuple[int, int, int]
    route_working_area_line_thickness_px: int
    route_swath_colour_bgr: tuple[int, int, int]
    route_swath_line_thickness_px: int
    route_connector_colour_bgr: tuple[int, int, int]
    route_connector_line_thickness_px: int
    route_start_marker_colour_bgr: tuple[int, int, int]
    route_start_marker_radius_px: int
    route_label_every_n_passes: int
    route_label_font_scale: float
    route_label_line_thickness_px: int
    route_label_offset_x_px: int
    route_label_offset_y_px: int


@dataclass(frozen=True)
class UiSettings:
    host: str
    port: int
    open_browser: bool
    browser_open_delay_s: float


@dataclass(frozen=True)
class Settings:
    mapbox: MapboxSettings
    gps: GpsSettings
    image_review: ImageReviewSettings
    computer_vision: ComputerVisionSettings
    farm_setup: FarmSetupSettings
    field_discovery: FieldDiscoverySettings
    route: RouteSettings
    terrain: TerrainSettings
    contour_route: ContourRouteSettings
    visualisation: VisualisationSettings
    ui: UiSettings


def _build_settings() -> Settings:
    field_minimum_area_ha = _positive_number("FIELD_MIN_AREA_HA")
    field_maximum_area_ha = _positive_number("FIELD_MAX_AREA_HA")
    if field_maximum_area_ha <= field_minimum_area_ha:
        raise SettingsError("FIELD_MAX_AREA_HA must be larger than FIELD_MIN_AREA_HA.")

    minimum_editor_vertices = _positive_integer("REFINEMENT_MIN_VERTICES")
    maximum_editor_vertices = _positive_integer("REFINEMENT_MAX_VERTICES")
    if maximum_editor_vertices < minimum_editor_vertices:
        raise SettingsError(
            "REFINEMENT_MAX_VERTICES must be at least REFINEMENT_MIN_VERTICES."
        )

    prefilter_minimum_ratio = _positive_number("DISCOVERY_PREFILTER_MIN_AREA_RATIO")
    prefilter_maximum_ratio = _positive_number("DISCOVERY_PREFILTER_MAX_AREA_RATIO")
    if prefilter_maximum_ratio < prefilter_minimum_ratio:
        raise SettingsError(
            "DISCOVERY_PREFILTER_MAX_AREA_RATIO must be at least "
            "DISCOVERY_PREFILTER_MIN_AREA_RATIO."
        )

    return Settings(
        mapbox=MapboxSettings(
            token=_optional_text("MAPBOX_TOKEN"),
            satellite_style=_required_text("MAPBOX_SATELLITE_STYLE"),
            request_timeout_s=_positive_number("MAPBOX_REQUEST_TIMEOUT_S"),
            geocode_timeout_s=_positive_number("MAPBOX_GEOCODE_TIMEOUT_S"),
            geocode_country_code=_required_text("MAPBOX_GEOCODE_COUNTRY"),
            capture_zoom=_positive_number("MAP_ZOOM"),
            capture_width_px=_positive_integer("IMAGE_WIDTH"),
            capture_height_px=_positive_integer("IMAGE_HEIGHT"),
        ),
        gps=GpsSettings(
            mode=_required_text("GPS_MODE").lower(),
            mock_latitude=_number("MOCK_LAT"),
            mock_longitude=_number("MOCK_LON"),
            serial_port=_required_text("GPS_PORT"),
            baud_rate=_positive_integer("GPS_BAUD"),
            stable_samples_required=_positive_integer("GPS_SAMPLES"),
            fix_timeout_s=_positive_number("GPS_TIMEOUT"),
            serial_read_timeout_s=_positive_number("GPS_SERIAL_READ_TIMEOUT_S"),
            allow_float_rtk=_boolean("GPS_ALLOW_FLOAT"),
        ),
        image_review=ImageReviewSettings(
            enabled=_boolean("REVIEW_IMAGE"),
            preview_width_px=_positive_integer("REVIEW_IMAGE_MAX_WIDTH_PX"),
            preview_height_px=_positive_integer("REVIEW_IMAGE_MAX_HEIGHT_PX"),
        ),
        computer_vision=ComputerVisionSettings(
            sam_model_id=_required_text("SAM_MODEL"),
            single_field_simplify_fraction=_fraction("BOUNDARY_SIMPLIFY"),
        ),
        farm_setup=FarmSetupSettings(
            overview_radius_m=_positive_number("FARM_OVERVIEW_RADIUS_M"),
            overview_width_px=_positive_integer("FARM_OVERVIEW_WIDTH"),
            overview_height_px=_positive_integer("FARM_OVERVIEW_HEIGHT"),
            minimum_selection_box_px=_positive_number("FARM_SELECTION_MIN_BOX_SIZE_PX"),
        ),
        field_discovery=FieldDiscoverySettings(
            discovery_image_width_px=_positive_integer("DISCOVERY_IMAGE_WIDTH"),
            discovery_image_height_px=_positive_integer("DISCOVERY_IMAGE_HEIGHT"),
            discovery_context_margin_m=_nonnegative_number("DISCOVERY_CONTEXT_MARGIN_M"),
            discovery_prompt_spacing_m=_positive_number("DISCOVERY_PROMPT_SPACING_M"),
            discovery_minimum_prompt_spacing_px=_positive_integer("DISCOVERY_MIN_PROMPT_SPACING_PX"),
            discovery_minimum_sam_score=_fraction("DISCOVERY_MIN_SAM_SCORE"),
            refinement_image_width_px=_positive_integer("REFINEMENT_IMAGE_WIDTH"),
            refinement_image_height_px=_positive_integer("REFINEMENT_IMAGE_HEIGHT"),
            refinement_target_metres_per_pixel=_positive_number("REFINEMENT_MPP"),
            refinement_margin_fraction=_fraction("REFINEMENT_MARGIN_FRACTION"),
            refinement_minimum_margin_m=_positive_number("REFINEMENT_MIN_MARGIN_M"),
            refinement_box_padding_m=_nonnegative_number("REFINEMENT_BOX_PADDING_M"),
            refinement_minimum_sam_score=_fraction("REFINEMENT_MIN_SAM_SCORE"),
            refinement_minimum_rough_overlap_fraction=_fraction("REFINEMENT_MIN_ROUGH_OVERLAP"),
            minimum_field_area_ha=field_minimum_area_ha,
            maximum_field_area_ha=field_maximum_area_ha,
            prefilter_minimum_area_ratio=prefilter_minimum_ratio,
            prefilter_maximum_area_ratio=prefilter_maximum_ratio,
            minimum_search_overlap_fraction=_fraction("FIELD_DISCOVERY_MIN_AREA_OVERLAP"),
            deduplication_iou_threshold=_fraction("FIELD_DEDUP_IOU"),
            simplify_tolerance_m=_positive_number("REFINEMENT_SIMPLIFY_M"),
            maximum_editor_vertices=maximum_editor_vertices,
            minimum_editor_vertices=minimum_editor_vertices,
            minimum_simplify_tolerance_m=_positive_number("REFINEMENT_MIN_SIMPLIFY_M"),
            simplify_maximum_iterations=_positive_integer("REFINEMENT_SIMPLIFY_MAX_ITERATIONS"),
            simplify_tolerance_growth=_positive_number("REFINEMENT_SIMPLIFY_GROWTH"),
        ),
        route=RouteSettings(
            tractor_profile=_required_text("TRACTOR_PROFILE"),
            implement_width_m=_positive_number("IMPLEMENT_WIDTH_M"),
            headland_width_m=_nonnegative_number("HEADLAND_WIDTH_M"),
            pass_overlap_fraction=_fraction("OVERLAP", upper=0.95),
            straight_angle_step_deg=_positive_number("ANGLE_STEP_DEG"),
            working_speed_mps=_positive_number("WORKING_SPEED_MPS"),
            turning_speed_mps=_positive_number("TURNING_SPEED_MPS"),
            fixed_turn_time_s=_nonnegative_number("FIXED_TURN_TIME_S"),
        ),
        terrain=TerrainSettings(
            auto_fetch=_boolean("AUTO_FETCH_TERRAIN"),
            terrain_tile_zoom=_positive_integer("TERRAIN_TILE_ZOOM"),
            grid_long_side_px=_positive_integer("TERRAIN_GRID_SIZE"),
            contour_interval_m=_positive_number("CONTOUR_INTERVAL_M"),
            contour_slope_threshold_deg=_nonnegative_number("CONTOUR_SLOPE_THRESHOLD_DEG"),
            request_timeout_s=_positive_number("TERRAIN_REQUEST_TIMEOUT"),
            overlay_dpi=_positive_integer("TERRAIN_OVERLAY_DPI"),
            overlay_contour_line_width=_positive_number(
                "TERRAIN_OVERLAY_CONTOUR_LINE_WIDTH"
            ),
            overlay_label_font_size=_positive_number(
                "TERRAIN_OVERLAY_LABEL_FONT_SIZE"
            ),
            overlay_boundary_line_width=_positive_number(
                "TERRAIN_OVERLAY_BOUNDARY_LINE_WIDTH"
            ),
        ),
        contour_route=ContourRouteSettings(
            angle_step_deg=_positive_number("CONTOUR_STRAIGHT_ANGLE_STEP_DEG"),
            alignment_weight=_nonnegative_number("CONTOUR_ALIGNMENT_WEIGHT"),
            minimum_direction_slope_deg=_nonnegative_number("CONTOUR_DIRECTION_MIN_SLOPE_DEG"),
            extra_overlap_fraction=_fraction("CONTOUR_EXTRA_OVERLAP", upper=0.50),
        ),
        visualisation=VisualisationSettings(
            detected_boundary_colour_bgr=_bgr_colour("DETECTED_BOUNDARY_COLOUR_BGR"),
            detected_boundary_line_thickness_px=_positive_integer(
                "DETECTED_BOUNDARY_LINE_THICKNESS_PX"
            ),
            gps_marker_colour_bgr=_bgr_colour("GPS_MARKER_COLOUR_BGR"),
            gps_marker_radius_px=_positive_integer("GPS_MARKER_RADIUS_PX"),
            candidate_boundary_colour_bgr=_bgr_colour("CANDIDATE_BOUNDARY_COLOUR_BGR"),
            candidate_boundary_line_thickness_px=_positive_integer(
                "CANDIDATE_BOUNDARY_LINE_THICKNESS_PX"
            ),
            route_field_colour_bgr=_bgr_colour("ROUTE_FIELD_COLOUR_BGR"),
            route_field_line_thickness_px=_positive_integer(
                "ROUTE_FIELD_LINE_THICKNESS_PX"
            ),
            route_working_area_colour_bgr=_bgr_colour("ROUTE_WORKING_AREA_COLOUR_BGR"),
            route_working_area_line_thickness_px=_positive_integer(
                "ROUTE_WORKING_AREA_LINE_THICKNESS_PX"
            ),
            route_swath_colour_bgr=_bgr_colour("ROUTE_SWATH_COLOUR_BGR"),
            route_swath_line_thickness_px=_positive_integer(
                "ROUTE_SWATH_LINE_THICKNESS_PX"
            ),
            route_connector_colour_bgr=_bgr_colour("ROUTE_CONNECTOR_COLOUR_BGR"),
            route_connector_line_thickness_px=_positive_integer(
                "ROUTE_CONNECTOR_LINE_THICKNESS_PX"
            ),
            route_start_marker_colour_bgr=_bgr_colour("ROUTE_START_MARKER_COLOUR_BGR"),
            route_start_marker_radius_px=_positive_integer("ROUTE_START_MARKER_RADIUS_PX"),
            route_label_every_n_passes=_positive_integer("ROUTE_LABEL_EVERY_N_PASSES"),
            route_label_font_scale=_positive_number("ROUTE_LABEL_FONT_SCALE"),
            route_label_line_thickness_px=_positive_integer(
                "ROUTE_LABEL_LINE_THICKNESS_PX"
            ),
            route_label_offset_x_px=_integer("ROUTE_LABEL_OFFSET_X_PX"),
            route_label_offset_y_px=_integer("ROUTE_LABEL_OFFSET_Y_PX"),
        ),
        ui=UiSettings(
            host=_required_text("GPS_TRACTOR_UI_HOST"),
            port=_positive_integer("GPS_TRACTOR_UI_PORT"),
            open_browser=_boolean("GPS_TRACTOR_OPEN_BROWSER"),
            browser_open_delay_s=_nonnegative_number("GPS_TRACTOR_BROWSER_OPEN_DELAY_S"),
        ),
    )


settings = _build_settings()
