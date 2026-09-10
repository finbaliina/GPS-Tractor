from __future__ import annotations

import json
import threading
import webbrowser

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from farm_data import (
    FARMS_DIR,
    create_farm,
    delete_farm,
    delete_field,
    field_dir,
    get_farm,
    get_field,
    import_capture_as_field,
    list_capture_candidates,
    list_farms,
    reset_boundary,
    save_boundary,
    save_field_obstacles,
)
from farm_setup import SETUP_CACHE, build_setup_preview, complete_setup, load_setup_preview
from field_discovery import (
    approve_candidate,
    approve_sequential_candidate,
    discover_fields,
    get_next_sequential_candidate,
    list_candidates,
    prepare_sequential_discovery,
    sequential_discovery_stats,
    set_candidate_status,
    set_sequential_candidate_status,
)
from field_routes import regenerate_field_route
from settings import settings


app = Flask(__name__)

DISCOVERY_JOBS: dict[str, dict] = {}
DISCOVERY_JOBS_LOCK = threading.Lock()
SEQUENTIAL_JOBS: dict[str, dict] = {}
SEQUENTIAL_JOBS_LOCK = threading.Lock()


def _update_discovery_job(farm_id: str, **changes) -> None:
    with DISCOVERY_JOBS_LOCK:
        current = DISCOVERY_JOBS.setdefault(farm_id, {})
        current.update(changes)


def _run_discovery_job(farm_id: str, force_rescan: bool) -> None:
    def progress_update(progress: dict) -> None:
        _update_discovery_job(farm_id, **progress)

    try:
        result = discover_fields(
            farm_id, force=force_rescan, progress_callback=progress_update
        )
        _update_discovery_job(
            farm_id,
            running=False,
            complete=True,
            error=None,
            percent=100,
            message=f"Field scan complete: {result['candidate_count']} candidates ready to review.",
            review_url=f"/farm/{farm_id}/review-fields",
        )
    except Exception as error:
        app.logger.exception("Background field discovery failed")
        _update_discovery_job(
            farm_id, running=False, complete=False, error=str(error),
            message=f"Field scan failed: {error}",
        )


@app.get("/")
def home():
    return render_template("home.html", farms=list_farms())


# Small development escape hatch. Normal setup should start at /setup.
@app.post("/farms")
def new_farm():
    farm_name = request.form.get("name", "")
    farm = create_farm(farm_name)
    return redirect(url_for("farm_page", farm_id=farm["id"]))


@app.post("/farm/<farm_id>/delete")
def delete_farm_route(farm_id):
    with DISCOVERY_JOBS_LOCK:
        running_job = DISCOVERY_JOBS.get(farm_id, {})
        if running_job.get("running"):
            return "Cannot delete a farm while field discovery is running.", 409

    delete_farm(farm_id)
    with DISCOVERY_JOBS_LOCK:
        DISCOVERY_JOBS.pop(farm_id, None)
    return redirect(url_for("home"))


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
        setup_preview = build_setup_preview(farm_name, postcode)
    except Exception as error:
        app.logger.exception("Farm setup lookup failed")
        return (
            render_template(
                "setup_search.html",
                error=str(error),
                farm_name=farm_name,
                postcode=postcode,
            ),
            400,
        )

    return redirect(url_for("setup_area", preview_id=setup_preview["id"]))


@app.get("/setup/<preview_id>/area")
def setup_area(preview_id):
    setup_preview = load_setup_preview(preview_id)
    return render_template("setup_area.html", preview=setup_preview, error=None)


@app.get("/setup/<preview_id>/overview.png")
def setup_overview_image(preview_id):
    load_setup_preview(preview_id)  # Validate the ID before constructing the path.
    return send_file(SETUP_CACHE / preview_id / "overview.png")


@app.post("/setup/<preview_id>/complete")
def setup_complete(preview_id):
    encoded_areas = request.form.get("areas_json", "[]")

    try:
        selected_rectangles = json.loads(encoded_areas)
        if not isinstance(selected_rectangles, list):
            raise ValueError("Selected areas are not in the expected format.")
        farm = complete_setup(preview_id, selected_rectangles)
    except Exception as error:
        app.logger.exception("Completing farm setup failed")
        return (
            render_template(
                "setup_area.html",
                preview=load_setup_preview(preview_id),
                error=str(error),
            ),
            400,
        )

    return redirect(url_for("farm_page", farm_id=farm["id"]))


@app.get("/farm/<farm_id>")
def farm_page(farm_id):
    return _render_farm_page(farm_id)


@app.post("/farm/<farm_id>/discover-fields")
def discover_farm_fields(farm_id):
    try:
        force_rescan = request.form.get("force") == "1"
        discover_fields(farm_id, force=force_rescan)
    except Exception as error:
        app.logger.exception("Field discovery failed")
        return _render_farm_page(farm_id, discovery_error=str(error), status_code=500)

    return redirect(url_for("review_fields", farm_id=farm_id))


@app.post("/api/farm/<farm_id>/discover-fields/start")
def start_discovery_job(farm_id):
    with DISCOVERY_JOBS_LOCK:
        existing = DISCOVERY_JOBS.get(farm_id, {})
        if existing.get("running"):
            return jsonify(existing)

    force_rescan = request.form.get("force") == "1"
    if request.is_json:
        force_rescan = bool((request.get_json(silent=True) or {}).get("force", False))

    _update_discovery_job(
        farm_id, running=True, complete=False, error=None, percent=0,
        message="Starting field scan...", review_url=None,
    )
    worker = threading.Thread(
        target=_run_discovery_job, args=(farm_id, force_rescan), daemon=True
    )
    worker.start()
    return jsonify(DISCOVERY_JOBS[farm_id])


@app.get("/api/farm/<farm_id>/discover-fields/status")
def discovery_job_status(farm_id):
    with DISCOVERY_JOBS_LOCK:
        status = dict(DISCOVERY_JOBS.get(farm_id, {
            "running": False, "complete": False, "percent": 0,
            "message": "No field scan is running.", "error": None,
        }))
    return jsonify(status)


@app.get("/farm/<farm_id>/review-fields")
def review_fields(farm_id):
    all_candidates = list_candidates(farm_id)
    pending_candidates = [
        candidate
        for candidate in all_candidates
        if candidate.get("status", "pending") == "pending"
    ]

    status_counts = {
        "pending": len(pending_candidates),
        "accepted": sum(
            candidate.get("status") == "accepted" for candidate in all_candidates
        ),
        "rejected": sum(
            candidate.get("status") == "rejected" for candidate in all_candidates
        ),
        "duplicate": sum(
            candidate.get("status") == "duplicate" for candidate in all_candidates
        ),
    }

    return render_template(
        "review_fields.html",
        farm=get_farm(farm_id),
        current=pending_candidates[0] if pending_candidates else None,
        counts=status_counts,
    )


@app.get("/farm/<farm_id>/candidate/<candidate_id>/overlay.png")
def candidate_overlay(farm_id, candidate_id):
    overlay_path = (
        FARMS_DIR / farm_id / "field_candidates" / candidate_id / "overlay.png"
    )
    return send_file(overlay_path)


@app.post("/farm/<farm_id>/candidate/<candidate_id>/accept")
def accept_candidate(farm_id, candidate_id):
    field_name = request.form.get("field_name", "")
    field = approve_candidate(farm_id, candidate_id, field_name)
    return redirect(url_for("boundary_editor", farm_id=farm_id, field_id=field["id"]))


@app.post("/farm/<farm_id>/candidate/<candidate_id>/accept-cv")
def accept_candidate_cv_boundary(farm_id, candidate_id):
    field_name = request.form.get("field_name", "")
    approve_candidate(farm_id, candidate_id, field_name)
    return redirect(url_for("review_fields", farm_id=farm_id))


@app.post("/farm/<farm_id>/candidate/<candidate_id>/reject")
def reject_candidate(farm_id, candidate_id):
    set_candidate_status(farm_id, candidate_id, "rejected")
    return redirect(url_for("review_fields", farm_id=farm_id))


@app.post("/farm/<farm_id>/candidate/<candidate_id>/duplicate")
def duplicate_candidate(farm_id, candidate_id):
    set_candidate_status(farm_id, candidate_id, "duplicate")
    return redirect(url_for("review_fields", farm_id=farm_id))


@app.post("/farm/<farm_id>/field/<field_id>/delete")
def delete_farm_field(farm_id, field_id):
    delete_field(farm_id, field_id)
    return redirect(url_for("farm_page", farm_id=farm_id))


@app.post("/farm/<farm_id>/fields")
def add_field(farm_id):
    capture_id = request.form.get("capture_id", "")
    field_name = request.form.get("field_name", "")
    field = import_capture_as_field(farm_id, capture_id, field_name)
    return redirect(url_for("boundary_editor", farm_id=farm_id, field_id=field["id"]))



def _update_sequential_job(farm_id: str, **changes) -> None:
    with SEQUENTIAL_JOBS_LOCK:
        current = SEQUENTIAL_JOBS.setdefault(farm_id, {})
        current.update(changes)


def _run_sequential_prepare_job(farm_id: str, force_rescan: bool) -> None:
    def progress_update(progress: dict) -> None:
        _update_sequential_job(farm_id, **progress)
    try:
        result = prepare_sequential_discovery(
            farm_id, force=force_rescan, progress_callback=progress_update
        )
        _update_sequential_job(
            farm_id, running=False, complete=True, error=None, percent=100,
            message=f"Sequential scan ready: {result['candidate_count']} rough possibilities ranked.",
            review_url=f"/farm/{farm_id}/sequential-fields",
        )
    except Exception as error:
        app.logger.exception("Sequential field discovery failed")
        _update_sequential_job(
            farm_id, running=False, complete=False, error=str(error),
            message=f"Sequential scan failed: {error}",
        )


@app.post("/api/farm/<farm_id>/sequential-fields/start")
def start_sequential_discovery_job(farm_id):
    with SEQUENTIAL_JOBS_LOCK:
        existing = SEQUENTIAL_JOBS.get(farm_id, {})
        if existing.get("running"):
            return jsonify(existing)
    force_rescan = request.form.get("force") == "1"
    if request.is_json:
        force_rescan = bool((request.get_json(silent=True) or {}).get("force", False))
    _update_sequential_job(
        farm_id, running=True, complete=False, error=None, percent=0,
        message="Starting sequential field scan...", review_url=None,
    )
    worker = threading.Thread(
        target=_run_sequential_prepare_job, args=(farm_id, force_rescan), daemon=True
    )
    worker.start()
    return jsonify(SEQUENTIAL_JOBS[farm_id])


@app.get("/api/farm/<farm_id>/sequential-fields/status")
def sequential_discovery_job_status(farm_id):
    with SEQUENTIAL_JOBS_LOCK:
        status = dict(SEQUENTIAL_JOBS.get(farm_id, {
            "running": False, "complete": False, "percent": 0,
            "message": "No sequential field scan is running.", "error": None,
        }))
    return jsonify(status)


@app.get("/farm/<farm_id>/sequential-fields")
def sequential_fields(farm_id):
    pool_path = FARMS_DIR / farm_id / "sequential_discovery" / "rough_pool.json"
    if not pool_path.exists():
        return _render_farm_page(
            farm_id,
            discovery_error=(
                "Sequential discovery has not been prepared for this farm yet. "
                "Click Start sequential scanner first."
            ),
        )

    current = get_next_sequential_candidate(farm_id)
    return render_template(
        "sequential_fields.html", farm=get_farm(farm_id), current=current,
        stats=sequential_discovery_stats(farm_id),
    )


@app.get("/farm/<farm_id>/sequential-fields/current/overlay.png")
def sequential_candidate_overlay(farm_id):
    return send_file(FARMS_DIR / farm_id / "sequential_discovery" / "current" / "overlay.png")


@app.post("/farm/<farm_id>/sequential-fields/current/accept")
def accept_sequential_field(farm_id):
    field_name = request.form.get("field_name", "")
    field = approve_sequential_candidate(farm_id, field_name)
    return redirect(url_for("boundary_editor", farm_id=farm_id, field_id=field["id"]))


@app.post("/farm/<farm_id>/sequential-fields/current/accept-cv")
def accept_sequential_cv_boundary(farm_id):
    field_name = request.form.get("field_name", "")
    approve_sequential_candidate(farm_id, field_name)
    return redirect(url_for("sequential_fields", farm_id=farm_id))


@app.post("/farm/<farm_id>/sequential-fields/current/reject")
def reject_sequential_field(farm_id):
    set_sequential_candidate_status(farm_id, "rejected")
    return redirect(url_for("sequential_fields", farm_id=farm_id))


@app.post("/farm/<farm_id>/sequential-fields/current/duplicate")
def duplicate_sequential_field(farm_id):
    set_sequential_candidate_status(farm_id, "duplicate")
    return redirect(url_for("sequential_fields", farm_id=farm_id))


@app.post("/farm/<farm_id>/sequential-fields/reset")
def reset_sequential_fields(farm_id):
    prepare_sequential_discovery(farm_id, force=True)
    return redirect(url_for("sequential_fields", farm_id=farm_id))


@app.get("/farm/<farm_id>/field/<field_id>/edit")
def boundary_editor(farm_id, field_id):
    field = get_field(farm_id, field_id)

    # Fields created during setup should return to the review queue they came
    # from. Sequential and normal discovery use separate queues.
    review_url = None
    if field.get("source") == "sequential_field_discovery":
        review_url = url_for("sequential_fields", farm_id=farm_id)
    elif field.get("source_candidate"):
        review_url = url_for("review_fields", farm_id=farm_id)

    return render_template(
        "boundary_editor.html",
        farm=get_farm(farm_id),
        field=field,
        review_url=review_url,
    )


@app.get("/farm/<farm_id>/field/<field_id>/satellite.png")
def field_satellite(farm_id, field_id):
    return send_file(field_dir(farm_id, field_id) / "satellite.png")


@app.get("/farm/<farm_id>/field/<field_id>/route_overlay.png")
def field_route_overlay(farm_id, field_id):
    route_overlay_path = field_dir(farm_id, field_id) / "route_overlay.png"
    if not route_overlay_path.exists():
        return "Route not generated", 404
    return send_file(route_overlay_path)


@app.get("/api/farm/<farm_id>/field/<field_id>")
def field_api(farm_id, field_id):
    return jsonify(get_field(farm_id, field_id))


@app.post("/api/farm/<farm_id>/field/<field_id>/boundary")
def save_boundary_api(farm_id, field_id):
    request_payload = request.get_json(force=True)
    boundary_points = request_payload.get("points", [])
    boundary_source = request_payload.get("source", "manual_edit")

    save_boundary(
        farm_id=farm_id,
        field_id=field_id,
        points=boundary_points,
        source=boundary_source,
    )
    if "obstacles" in request_payload:
        save_field_obstacles(
            farm_id=farm_id,
            field_id=field_id,
            obstacles=request_payload.get("obstacles", []),
        )
    return jsonify({"ok": True, "points": boundary_points})


@app.post("/api/farm/<farm_id>/field/<field_id>/boundary/reset")
def reset_boundary_api(farm_id, field_id):
    boundary_points = reset_boundary(farm_id, field_id)
    return jsonify({"ok": True, "points": boundary_points})


@app.post("/api/farm/<farm_id>/field/<field_id>/route")
def regenerate_route_api(farm_id, field_id):
    try:
        route_result = regenerate_field_route(farm_id, field_id)
        return jsonify(route_result)
    except Exception as error:
        app.logger.exception("Route generation failed")
        return jsonify({"ok": False, "error": str(error)}), 500


@app.post("/farm/<farm_id>/calculate-all-routes")
def calculate_all_routes(farm_id):
    farm = get_farm(farm_id)
    failures = []
    completed = 0
    for field in farm.get("fields", []):
        try:
            regenerate_field_route(farm_id, field["id"])
            completed += 1
        except Exception as error:
            app.logger.exception("Route generation failed for %s", field["id"])
            failures.append(f"{field['name']}: {error}")

    if failures:
        message = (
            f"Calculated routes for {completed} field(s). "
            f"Could not calculate {len(failures)}: " + "; ".join(failures)
        )
        return _render_farm_page(farm_id, discovery_error=message, status_code=400)

    return redirect(url_for("farm_page", farm_id=farm_id))


@app.get("/farm/<farm_id>/field/<field_id>/drive")
def drive_preview(farm_id, field_id):
    return render_template(
        "drive.html",
        farm=get_farm(farm_id),
        field=get_field(farm_id, field_id),
    )


def _render_farm_page(
    farm_id: str,
    discovery_error: str | None = None,
    status_code: int = 200,
):
    """Build the farm page context in one place."""
    all_candidates = list_candidates(farm_id)
    pending_candidate_count = sum(
        candidate.get("status", "pending") == "pending"
        for candidate in all_candidates
    )

    response = render_template(
        "farm.html",
        farm=get_farm(farm_id),
        captures=list_capture_candidates(),
        candidate_count=len(all_candidates),
        pending_candidate_count=pending_candidate_count,
        discovery_error=discovery_error,
        sequential_ready=(FARMS_DIR / farm_id / "sequential_discovery" / "rough_pool.json").exists(),
    )
    return response, status_code


def main() -> None:
    ui_settings = settings.ui
    local_url = f"http://{ui_settings.host}:{ui_settings.port}"

    print("\nGPS Tractor UI")
    print(f"Open: {local_url}\n")

    if ui_settings.open_browser:
        browser_timer = threading.Timer(
            ui_settings.browser_open_delay_s,
            lambda: webbrowser.open(local_url),
        )
        browser_timer.daemon = True
        browser_timer.start()

    app.run(
        host=ui_settings.host,
        port=ui_settings.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
