"""Geometry helpers shared by field detection and route generation."""

from __future__ import annotations

from shapely.geometry import Polygon


def clean_polygon(polygon: Polygon) -> Polygon:
    """Repair a polygon and return its largest polygon component."""
    cleaned_geometry = polygon if polygon.is_valid else polygon.buffer(0)

    if cleaned_geometry.is_empty:
        raise ValueError("Polygon is empty after geometry repair.")

    if cleaned_geometry.geom_type == "MultiPolygon":
        cleaned_geometry = max(cleaned_geometry.geoms, key=lambda part: part.area)

    if cleaned_geometry.geom_type != "Polygon":
        raise ValueError(f"Expected Polygon, got {cleaned_geometry.geom_type}.")

    return cleaned_geometry
