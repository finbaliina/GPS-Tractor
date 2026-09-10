from contour_planning import plan_contour_route, save_contour_route_plan
from field_boundary import detect_field
from gps_location import get_position
from image_review import review_image
from route_planning import plan_route, save_route_plan
from route_visualisation import draw_route
from satellite_image import capture_satellite_image
from settings import settings
from terrain import get_terrain


def main() -> None:
    print("\nGPS Tractor\n")

    print("1/6  Getting position...")
    position = get_position()

    print("2/6  Downloading satellite image...")
    satellite_capture = capture_satellite_image(position)

    print("3/6  Confirming image...")
    image_was_accepted = review_image(
        satellite_capture.image_path,
        satellite_capture.metadata_path,
        position,
    )
    if not image_was_accepted:
        print("Image rejected. Stopping.")
        return

    print("4/6  Detecting field boundary...")
    detected_boundary = detect_field(
        satellite_capture.image_path,
        satellite_capture.folder,
    )

    print("5/6  Reading terrain...")
    terrain_data = get_terrain(satellite_capture, detected_boundary.polygon)

    print("6/6  Planning route...")
    route_plan = _plan_route_for_terrain(
        field_polygon=detected_boundary.polygon,
        terrain_data=terrain_data,
        metres_per_pixel=satellite_capture.metres_per_pixel,
        output_directory=satellite_capture.folder,
    )

    route_overlay_path = satellite_capture.folder / "route_overlay.png"
    draw_route(
        satellite_capture.image_path,
        detected_boundary.polygon,
        route_plan,
        route_overlay_path,
    )

    print("\nFinished.")
    print(f"Capture folder: {satellite_capture.folder}")
    print(f"Boundary:       {detected_boundary.overlay_path}")
    print(f"Terrain:        {terrain_data.overlay_path}")
    print(f"Route:          {route_overlay_path}")


def _plan_route_for_terrain(
    field_polygon,
    terrain_data,
    metres_per_pixel: float,
    output_directory,
):
    slope_threshold_deg = settings.terrain.contour_slope_threshold_deg
    route_plan_path = output_directory / "route_plan.json"

    if terrain_data.p90_slope_deg >= slope_threshold_deg:
        print(
            "Terrain exceeds contour threshold "
            f"({terrain_data.p90_slope_deg:.1f}° >= {slope_threshold_deg:.1f}°)."
        )
        print("Using terrain-aligned straight route planner.")

        route_plan = plan_contour_route(
            field_polygon,
            terrain_data,
            metres_per_pixel,
        )
        save_contour_route_plan(route_plan, route_plan_path)
        return route_plan

    print(
        "Terrain below contour threshold "
        f"({terrain_data.p90_slope_deg:.1f}° < {slope_threshold_deg:.1f}°)."
    )
    print("Using straight-line route planner.")

    route_plan = plan_route(field_polygon, metres_per_pixel)
    save_route_plan(route_plan, route_plan_path)
    return route_plan


if __name__ == "__main__":
    main()
