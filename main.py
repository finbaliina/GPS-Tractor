from dotenv import load_dotenv

from gps_location import get_position
from satellite_image import capture_satellite_image
from image_review import review_image
from field_boundary import detect_field
from route_planning import plan_route, save_route_plan
from route_visualisation import draw_route


def main():
    load_dotenv()

    print("\nGPS Tractor\n")

    print("1/5  Getting position...")
    position = get_position()

    print("2/5  Downloading satellite image...")
    capture = capture_satellite_image(position)

    print("3/5  Confirming image...")
    if not review_image(capture.image_path, capture.metadata_path, position):
        print("Image rejected. Stopping.")
        return

    print("4/5  Detecting field boundary...")
    boundary = detect_field(capture.image_path, capture.folder)

    print("5/5  Planning route...")
    plan = plan_route(boundary.polygon, capture.metres_per_pixel)
    save_route_plan(plan, capture.folder / "route_plan.json")
    draw_route(capture.image_path, boundary.polygon, plan, capture.folder / "route_overlay.png")

    print("\nFinished.")
    print(f"Capture folder: {capture.folder}")
    print(f"Boundary:       {boundary.overlay_path}")
    print(f"Route:          {capture.folder / 'route_overlay.png'}")


if __name__ == "__main__":
    main()
