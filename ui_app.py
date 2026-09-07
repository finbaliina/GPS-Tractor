from __future__ import annotations

import os
import webbrowser

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from cadastral_lookup import data_status
from farm_data import (
    create_farm,
    field_dir,
    get_farm,
    get_field,
    import_capture_as_field,
    list_capture_candidates,
    list_farms,
    reset_boundary,
    save_boundary,
)
from farm_setup import (
    build_setup_preview,
    complete_setup,
    load_setup_preview,
    lonlat_to_pixel,
    polygon_to_pixels,
)
from field_routes import regenerate_field_route


app = Flask(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@app.get("/")
def home():
    return render_template("home.html", farms=list_farms())


# Legacy/manual farm creation remains available for development and unusual cases.
@app.post("/farms")
def new_farm():
    name = request.form.get("name", "")
    farm = create_farm(name)
    return redirect(url_for("farm_page", farm_id=farm["id"]))


@app.route("/setup", methods=["GET", "POST"])
def setup_search():
    if request.method == "GET":
        return render_template(
            "setup_search.html",
            data_status=data_status(),
            demo_enabled=_env_bool("CADASTRAL_DEMO", False),
            error=None,
            farm_name="",
            postcode="",
        )

    farm_name = request.form.get("farm_name", "")
    postcode = request.form.get("postcode", "")
    try:
        preview = build_setup_preview(farm_name, postcode)
    except Exception as exc:
        app.logger.exception("Farm setup lookup failed")
        return render_template(
            "setup_search.html",
            data_status=data_status(),
            demo_enabled=_env_bool("CADASTRAL_DEMO", False),
            error=str(exc),
            farm_name=farm_name,
            postcode=postcode,
        ), 400

    return redirect(url_for("setup_parcels", preview_id=preview["id"]))


@app.get("/setup/<preview_id>/parcels")
def setup_parcels(preview_id):
    preview = load_setup_preview(preview_id)
    for parcel in preview["parcels"]:
        parcel["pixel_rings"] = polygon_to_pixels(parcel["geometry"], preview["map"])

    preview["search_point_px"] = lonlat_to_pixel(
        preview["location"]["longitude"],
        preview["location"]["latitude"],
        preview["map"],
    )
    parcel_info = {
        p["id"]: {
            "area_m2": p["area_m2"],
            "contains_search_point": p["contains_search_point"],
        }
        for p in preview["parcels"]
    }
    return render_template(
        "setup_parcels.html",
        preview=preview,
        parcel_info=parcel_info,
        buffer_m=float(os.getenv("FARM_EXTRA_VIEW_BUFFER_M", "800")),
    )


@app.get("/setup/<preview_id>/overview.png")
def setup_overview_image(preview_id):
    path = os.path.join("setup_cache", preview_id, "overview.png")
    return send_file(path)


@app.post("/setup/<preview_id>/complete")
def setup_complete(preview_id):
    selected_ids = [
        item for item in request.form.get("selected_ids", "").split("|") if item
    ]
    try:
        farm = complete_setup(preview_id, selected_ids)
    except Exception as exc:
        app.logger.exception("Completing farm setup failed")
        preview = load_setup_preview(preview_id)
        for parcel in preview["parcels"]:
            parcel["pixel_rings"] = polygon_to_pixels(parcel["geometry"], preview["map"])
        preview["search_point_px"] = lonlat_to_pixel(
            preview["location"]["longitude"],
            preview["location"]["latitude"],
            preview["map"],
        )
        parcel_info = {
            p["id"]: {
                "area_m2": p["area_m2"],
                "contains_search_point": p["contains_search_point"],
            }
            for p in preview["parcels"]
        }
        return render_template(
            "setup_parcels.html",
            preview=preview,
            parcel_info=parcel_info,
            buffer_m=float(os.getenv("FARM_EXTRA_VIEW_BUFFER_M", "800")),
            error=str(exc),
        ), 400

    return redirect(url_for("farm_page", farm_id=farm["id"]))


@app.get("/farm/<farm_id>")
def farm_page(farm_id):
    return render_template(
        "farm.html",
        farm=get_farm(farm_id),
        captures=list_capture_candidates(),
    )


@app.post("/farm/<farm_id>/fields")
def add_field(farm_id):
    capture_id = request.form.get("capture_id", "")
    field_name = request.form.get("field_name", "")
    field = import_capture_as_field(farm_id, capture_id, field_name)

    return redirect(
        url_for(
            "boundary_editor",
            farm_id=farm_id,
            field_id=field["id"],
        )
    )


@app.get("/farm/<farm_id>/field/<field_id>/edit")
def boundary_editor(farm_id, field_id):
    return render_template(
        "boundary_editor.html",
        farm=get_farm(farm_id),
        field=get_field(farm_id, field_id),
    )


@app.get("/farm/<farm_id>/field/<field_id>/satellite.png")
def field_satellite(farm_id, field_id):
    return send_file(field_dir(farm_id, field_id) / "satellite.png")


@app.get("/farm/<farm_id>/field/<field_id>/route_overlay.png")
def field_route_overlay(farm_id, field_id):
    path = field_dir(farm_id, field_id) / "route_overlay.png"
    if not path.exists():
        return ("Route not generated", 404)
    return send_file(path)


@app.get("/api/farm/<farm_id>/field/<field_id>")
def field_api(farm_id, field_id):
    return jsonify(get_field(farm_id, field_id))


@app.post("/api/farm/<farm_id>/field/<field_id>/boundary")
def save_boundary_api(farm_id, field_id):
    payload = request.get_json(force=True)
    points = payload.get("points", [])
    source = payload.get("source", "manual_edit")

    save_boundary(
        farm_id=farm_id,
        field_id=field_id,
        points=points,
        source=source,
    )

    return jsonify({"ok": True, "points": points})


@app.post("/api/farm/<farm_id>/field/<field_id>/boundary/reset")
def reset_boundary_api(farm_id, field_id):
    points = reset_boundary(farm_id, field_id)
    return jsonify({"ok": True, "points": points})


@app.post("/api/farm/<farm_id>/field/<field_id>/route")
def regenerate_route_api(farm_id, field_id):
    try:
        result = regenerate_field_route(farm_id, field_id)
        return jsonify(result)
    except Exception as exc:
        app.logger.exception("Route generation failed")
        return jsonify({
            "ok": False,
            "error": str(exc),
        }), 500


@app.get("/farm/<farm_id>/field/<field_id>/drive")
def drive_preview(farm_id, field_id):
    return render_template(
        "drive.html",
        farm=get_farm(farm_id),
        field=get_field(farm_id, field_id),
    )


def main():
    port = int(os.getenv("GPS_TRACTOR_UI_PORT", "5000"))
    url = f"http://127.0.0.1:{port}"

    print("\nGPS Tractor UI")
    print(f"Open: {url}\n")

    if os.getenv("GPS_TRACTOR_OPEN_BROWSER", "true").lower() in {"1", "true", "yes"}:
        import threading
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
