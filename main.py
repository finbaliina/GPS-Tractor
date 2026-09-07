import os

from dotenv import load_dotenv

from gps_location import get_position
from satellite_image import capture_satellite_image
from image_review import review_image
from field_boundary import detect_field
from terrain import get_terrain
from route_planning import plan_route, save_route_plan
from contour_planning import plan_contour_route, save_contour_route_plan
from route_visualisation import draw_route


def main():
    load_dotenv()

    print("\nGPS Tractor\n")

    print("1/6  Getting position...")
    position = get_position()

    print("2/6  Downloading satellite image...")
    capture = capture_satellite_image(position)

    print("3/6  Confirming image...")
    if not review_image(capture.image_path, capture.metadata_path, position):
        print("Image rejected. Stopping.")
        return

    print("4/6  Detecting field boundary...")
    boundary = detect_field(capture.image_path, capture.folder)

    print("5/6  Reading terrain...")
    terrain = get_terrain(capture, boundary.polygon)

    print("6/6  Planning route...")
    threshold = float(os.getenv("CONTOUR_SLOPE_THRESHOLD_DEG", "5.0"))

    if terrain.p90_slope_deg >= threshold:
        print(
            f"Terrain exceeds contour threshold "
            f"({terrain.p90_slope_deg:.1f}° >= {threshold:.1f}°)."
        )
        print("Using contour-following route planner.")

        plan = plan_contour_route(
            boundary.polygon,
            terrain,
            capture.metres_per_pixel,
        )
        save_contour_route_plan(
            plan,
            capture.folder / "route_plan.json",
        )
    else:
        print(
            f"Terrain below contour threshold "
            f"({terrain.p90_slope_deg:.1f}° < {threshold:.1f}°)."
        )
        print("Using straight-line route planner.")

        plan = plan_route(
            boundary.polygon,
            capture.metres_per_pixel,
        )
        save_route_plan(
            plan,
            capture.folder / "route_plan.json",
        )

    draw_route(
        capture.image_path,
        boundary.polygon,
        plan,
        capture.folder / "route_overlay.png",
    )

    print("\nFinished.")
    print(f"Capture folder: {capture.folder}")
    print(f"Boundary:       {boundary.overlay_path}")
    print(f"Terrain:        {terrain.overlay_path}")
    print(f"Route:          {capture.folder / 'route_overlay.png'}")


if __name__ == "__main__":
    main()
