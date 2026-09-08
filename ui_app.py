from __future__ import annotations

import json
import os
import webbrowser

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

load_dotenv()

from farm_data import (
    FARMS_DIR,
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
from farm_setup import SETUP_CACHE, build_setup_preview, complete_setup, load_setup_preview
from field_routes import regenerate_field_route
from field_discovery import (
    approve_candidate,
    discover_fields,
    list_candidates,
    set_candidate_status,
)


app = Flask(__name__)


@app.get("/")
def home():
    return render_template("home.html", farms=list_farms())


# Kept as a small development/escape hatch. Normal users should use /setup.
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
            error=str(exc),
            farm_name=farm_name,
            postcode=postcode,
        ), 400

    return redirect(url_for("setup_area", preview_id=preview["id"]))


@app.get("/setup/<preview_id>/area")
def setup_area(preview_id):
    preview = load_setup_preview(preview_id)
    return render_template("setup_area.html", preview=preview, error=None)


@app.get("/setup/<preview_id>/overview.png")
def setup_overview_image(preview_id):
    preview = load_setup_preview(preview_id)  # validates ID before constructing path
    _ = preview
    path = SETUP_CACHE / preview_id / "overview.png"
    return send_file(path)


@app.post("/setup/<preview_id>/complete")
def setup_complete(preview_id):
    raw = request.form.get("areas_json", "[]")
    try:
        rectangles = json.loads(raw)
        if not isinstance(rectangles, list):
            raise ValueError("Selected areas are not in the expected format.")
        farm = complete_setup(preview_id, rectangles)
    except Exception as exc:
        app.logger.exception("Completing farm setup failed")
        return render_template(
            "setup_area.html",
            preview=load_setup_preview(preview_id),
            error=str(exc),
        ), 400

    return redirect(url_for("farm_page", farm_id=farm["id"]))


@app.get("/farm/<farm_id>")
def farm_page(farm_id):
    farm = get_farm(farm_id)
    candidates = list_candidates(farm_id)
    pending = sum(1 for c in candidates if c.get("status", "pending") == "pending")
    return render_template(
        "farm.html",
        farm=farm,
        captures=list_capture_candidates(),
        candidate_count=len(candidates),
        pending_candidate_count=pending,
    )


@app.post("/farm/<farm_id>/discover-fields")
def discover_farm_fields(farm_id):
    try:
        discover_fields(farm_id, force=request.form.get("force") == "1")
    except Exception as exc:
        app.logger.exception("Field discovery failed")
        candidates = list_candidates(farm_id)
        return render_template(
            "farm.html",
            farm=get_farm(farm_id),
            captures=list_capture_candidates(),
            candidate_count=len(candidates),
            pending_candidate_count=sum(
                1 for c in candidates if c.get("status", "pending") == "pending"
            ),
            discovery_error=str(exc),
        ), 500
    return redirect(url_for("review_fields", farm_id=farm_id))


@app.get("/farm/<farm_id>/review-fields")
def review_fields(farm_id):
    candidates = list_candidates(farm_id)
    pending = [c for c in candidates if c.get("status", "pending") == "pending"]
    counts = {
        "pending": len(pending),
        "accepted": sum(1 for c in candidates if c.get("status") == "accepted"),
        "rejected": sum(1 for c in candidates if c.get("status") == "rejected"),
    }
    return render_template(
        "review_fields.html",
        farm=get_farm(farm_id),
        current=pending[0] if pending else None,
        counts=counts,
    )


@app.get("/farm/<farm_id>/candidate/<candidate_id>/overlay.png")
def candidate_overlay(farm_id, candidate_id):
    path = FARMS_DIR / farm_id / "field_candidates" / candidate_id / "overlay.png"
    return send_file(path)


@app.post("/farm/<farm_id>/candidate/<candidate_id>/accept")
def accept_candidate(farm_id, candidate_id):
    field = approve_candidate(farm_id, candidate_id, request.form.get("field_name", ""))
    return redirect(url_for("boundary_editor", farm_id=farm_id, field_id=field["id"]))


@app.post("/farm/<farm_id>/candidate/<candidate_id>/reject")
def reject_candidate(farm_id, candidate_id):
    set_candidate_status(farm_id, candidate_id, "rejected")
    return redirect(url_for("review_fields", farm_id=farm_id))


@app.post("/farm/<farm_id>/fields")
def add_field(farm_id):
    capture_id = request.form.get("capture_id", "")
    field_name = request.form.get("field_name", "")
    field = import_capture_as_field(farm_id, capture_id, field_name)
    return redirect(
        url_for("boundary_editor", farm_id=farm_id, field_id=field["id"])
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
    save_boundary(farm_id=farm_id, field_id=field_id, points=points, source=source)
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
        return jsonify({"ok": False, "error": str(exc)}), 500


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
