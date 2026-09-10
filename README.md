# GPS Tractor

A local Python application for setting up a farm from satellite imagery, detecting field boundaries with SAM2, reviewing those boundaries, and generating straight or terrain-aware tractor routes.

The project has been refactored to keep the code easy to follow and to put **all tuneable application parameters in one place**.

## Configuration rule

`settings.py` is the only module that reads environment variables.

- `.env` contains the values used on this machine.
- `.env.example` is the documented reference for every tuneable value.
- Application modules use typed settings such as `settings.field_discovery.refinement_target_metres_per_pixel` rather than calling `os.getenv()` themselves.
- Constants that describe fixed formats or standards (for example Web Mercator limits, NMEA fix codes, Mapbox tile size and hectares-to-square-metres conversion) stay in Python because they are not tuning controls.

If you want to tune CV, imagery, routing, terrain or UI behaviour, start with `.env` / `.env.example` rather than searching through the Python files.

## Current workflow

### Farm setup

```text
farm name + postcode
        ↓
zoomed-out Mapbox satellite overview
        ↓
farmer draws one or more rough search boxes
        ↓
one whole-area satellite image is downloaded per selected area
        ↓
SAM2 roughly discovers candidate fields
        ↓
a dedicated high-resolution image is downloaded for each candidate
        ↓
SAM2 refines each field boundary
        ↓
farmer keeps/rejects candidates and edits the useful ones
        ↓
terrain data and a route are generated
        ↓
everything required for later use is stored locally
```

The setup boxes are deliberately rough. They are **search areas, not field boundaries**. It is better to include too much land than to cut off a field. Multiple boxes can be drawn for detached blocks of land.

Once a farm has been set up, the registry/geocoding/setup stage is not needed for normal operation. Fields can also be added later using the single-field GPS/capture workflow.

## Project layout

```text
main.py                 Single-field GPS/capture pipeline
ui_app.py               Local Flask user interface
settings.py             Typed configuration; the only environment reader

farm_setup.py           Farm geocoding, overview imagery and rough search boxes
field_discovery.py       Whole-area discovery, per-field SAM refinement and review
farm_data.py            Local farm/field storage
field_routes.py          Route generation from an approved field boundary

mapbox.py                Shared Mapbox, scale and Web-Mercator helpers
sam_utils.py             Shared SAM2 model/predictor setup
geometry_utils.py        Shared Shapely polygon cleanup
json_io.py               Small JSON read/write helpers

gps_location.py          Mock or RTK/NMEA position input
satellite_image.py       Single-position satellite capture
image_review.py          Single-position image confirmation
field_boundary.py        Single-field SAM boundary detection
terrain.py               Mapbox Terrain-RGB elevation and slope handling
route_planning.py        Normal straight-pass route planner
contour_planning.py      Straight passes aligned with contour direction
route_visualisation.py   Route overlay rendering
tractor_profiles.json    Tractor geometry profiles

templates/               Flask HTML templates
static/                  Browser-side CSS/JavaScript
notes/                   Project notes
```

Runtime folders are created as needed:

```text
farms/
captures/
setup_cache/
```

They are ignored by Git and are deliberately not included in the cleaned source package.

## Windows setup

From PowerShell:

```powershell
cd "C:\Users\Finlay\Documents\Projects\GPS Tractor\v1"

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install a suitable PyTorch build separately. For the existing NVIDIA/CUDA development machine:

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
python -m pip install git+https://github.com/facebookresearch/sam2.git
```

Check that PyTorch can see the GPU:

```powershell
python -c "import torch; print('CUDA:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

If starting from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Then put the real Mapbox token in `.env`.

## Running the application

For the farm UI:

```powershell
.\.venv\Scripts\Activate.ps1
python ui_app.py
```

or run `start_ui.bat`.

By default the browser opens at:

```text
http://127.0.0.1:5000
```

For the original single-field development pipeline:

```powershell
python main.py
```

## Configuration sections

`.env.example` contains a description immediately above every setting. The main groups are:

- **Mapbox / satellite imagery** — token, timeouts, styles, capture dimensions and zoom.
- **GPS / RTK** — mock coordinates, serial port, baud, sampling and fix requirements.
- **Single-field CV** — SAM model and single-field contour simplification.
- **Farm overview** — width, height, radius and minimum box size.
- **Field discovery/refinement** — whole-area image size and prompt spacing, per-field close-up resolution/margins, SAM confidence, field-size limits, deduplication and editable-boundary simplification.
- **Route planning** — implement width, headland, overlap, angle search, tractor speeds and turn penalty.
- **Terrain / contour-aware routing** — DEM resolution, slope threshold and terrain-alignment weighting.
- **Visualisation** — line colours, widths, labels and markers.
- **Local UI** — host, port and browser-launch behaviour.

The current scanner uses a two-stage design: one complete overview image for each selected search area, followed by a fresh high-resolution image and box-guided SAM pass for each rough candidate. These controls are exposed in `.env` so they can be tuned without modifying Python. No automatic tiling, mosaicing or stitching is used.

## Stored farm data

A typical farm looks like:

```text
farms/
    example_farm/
        farm.json
        overview.png
        farm_search_area.geojson
        field_discovery.json
        discovery_overviews/
        field_refinement/
        field_candidates/
        fields/
            north_field/
                field.json
                satellite.png
                metadata.json
                field_boundary_detected.json
                field_boundary_approved.json
                field_boundary.json
                terrain_data.npz
                terrain.json
                terrain_overlay.png
                route_plan.json
                route_overlay.png
```

`discovery_boundary.json` preserves the rough Stage-1 whole-area detection. `field_boundary_detected.json` preserves the refined Stage-2 CV result. The approved/working boundary can then be edited by the farmer without losing either CV stage.

## Route behaviour

The normal planner reserves a headland, creates parallel working passes, searches several headings and estimates work/turn time.

If the field is sufficiently steep, the terrain-aware planner still creates **straight passes**, but chooses a heading that better follows equal-elevation/contour direction.

The white connectors in `route_overlay.png` currently show route order only. They are not yet steering-constrained tractor turns.

## Git / cleanup

The cleaned project intentionally excludes:

- nested/duplicate historical copies of the project;
- `.git` internals from the uploaded archive;
- `__pycache__` and compiled Python files;
- generated farm imagery and field data;
- setup caches and captures;
- virtual environments.

The local `.env` is also ignored by Git because it contains the Mapbox token.

### Field-mask tuning

Whole-area discovery and close-up refinement now prefer the smallest SAM mask
when several alternatives have very similar confidence scores. This is intended
to reduce cases where adjoining fields are returned as one large mask.

Tune with:

- `DISCOVERY_MASK_SCORE_TOLERANCE` — Stage-1 overview selection.
- `REFINEMENT_MASK_SCORE_TOLERANCE` — close-up selection.
- `REFINEMENT_MAX_VERTICES` — maximum points shown in the boundary editor (15 by default).

Increasing a mask-score tolerance makes the scanner more willing to choose a
slightly lower-confidence, smaller mask. Reducing it makes SAM confidence more
dominant.

### Setup review and scan progress

The farm setup UI now supports optional field naming during candidate review. If the name is left blank, the candidate is saved as `Field no X`. Candidates can also be marked as duplicates without creating another configured field.

The farm overview shows a satellite thumbnail for every configured field and allows fields to be deleted (with confirmation). Automatic discovery runs through a background Flask worker and reports live progress to the farm page while SAM scans the overview and refines each candidate.

## Experimental sequential discovery branch

The farm page now also offers **Sequential field discovery**. This is deliberately
kept separate from the normal whole-area -> refine-all scanner so both approaches
can be compared on the same farm.

The sequential branch:

1. downloads/uses the same whole-area overview image;
2. runs the overview SAM prompt grid once and ranks rough candidates by confidence;
3. refines only the highest-ranked remaining candidate;
4. asks the farmer to keep, reject or mark it as a duplicate;
5. suppresses later rough candidates that substantially overlap an accepted field
   or an area marked as a duplicate;
6. does **not** suppress an entire area when a candidate is rejected, because a
   rejected merged/bad mask may still contain valid fields that should appear later;
7. repeats until the candidate pool is exhausted.

This is an experimental branch, not a replacement for the main scanner. Its main
purpose is to test whether human decisions made one field at a time give cleaner
farm setup than automatically refining a very large candidate list.

## Field obstacles and bulk route calculation

In the field editor, farmers can draw circular or square **avoid areas** around rocks, trees, wet patches or other obstacles. These are saved separately in `field_obstacles.json` and are cut out of the routeable field before route planning.

The farm page also includes **Calculate all routes**, which regenerates routes for every saved field in that farm.

## Clickable farm map and aligned obstacle rectangles

Pending field suggestions on the farm progress map are clickable. Selecting an amber outline opens that specific suggestion for review. If another suggestion was already being displayed, it is returned to the waiting queue rather than discarded.

The field boundary editor also includes **Draw rectangle**. This is a corner-to-corner, image-aligned rectangle: drag from one corner to the opposite corner and the sides remain horizontal/vertical on the satellite image. Circles and equal-sided squares remain available as separate obstacle tools.

## Human-in-the-loop UI additions

- Farm progress map can be panned and zoomed; pending field outlines remain clickable.
- Farm-area setup map can be zoomed with the mouse wheel and panned with Shift + drag while normal dragging still draws search boxes.
- Field review includes **Whole field not in image**, which retries the current field with a wider satellite view and a wider scan area.
- Field editing has a separate **Areas to avoid** toolbox. Circle, square, axis-aligned rectangle and angled rectangle avoid areas are saved and excluded from route planning.
- Angled rectangles are drawn in two steps: drag the first side to set direction and length, then click to set the width.
