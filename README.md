# GPS Tractor

This is the cleaned project version after dropping the cadastral/land-registry setup experiment.

## Current architecture

### Farm setup (online, once)

```text
farm name + postcode
        ↓
zoomed-out Mapbox satellite overview
        ↓
farmer draws one or more rough boxes around the land they operate
        ↓
save farm_search_area.geojson locally
        ↓
download detailed imagery only for the selected area
        ↓
SAM2 discovers candidate fields
        ↓
farmer keeps/rejects candidates and edits each boundary
        ↓
generate terrain-aware route and save it locally
```

The rough boxes are **search areas, not field boundaries**. It is better to include a little too much land than to clip a field at the edge.

Detached blocks are supported by drawing several boxes.

After setup, normal field use does not need the farm setup process again. Additional fields can be imported later from the existing GPS/capture pipeline.

### Single-field / development pipeline

`main.py` is still available for the original GPS-at-field workflow:

```text
GPS position
    ↓
Mapbox satellite image
    ↓
operator confirms image
    ↓
SAM field boundary
    ↓
Mapbox terrain
    ↓
route optimisation
```

## Main files

```text
main.py                 Original single-field command-line pipeline
ui_app.py               Local Flask farm UI
farm_setup.py            Farm search + broad satellite view + box selection
field_discovery.py       Detailed tiling + SAM field discovery + review candidates
farm_data.py             Local farm/field storage and boundary compatibility
field_routes.py          Regenerates route from approved field boundary

gps_location.py          Mock/RTK GPS position
satellite_image.py       Individual satellite capture
image_review.py          Individual capture confirmation
field_boundary.py        SAM boundary detection for single-field captures
terrain.py               Mapbox Terrain-RGB elevation/slope data
route_planning.py        Normal straight coverage planner
contour_planning.py      Straight passes fitted to contour direction
route_visualisation.py   Route overlay
tractor_profiles.json    Tractor geometry
```

Runtime data is stored under:

```text
farms/
captures/
setup_cache/
```

These are not committed to Git.

## Windows setup

From PowerShell:

```powershell
cd "C:\Users\Finlay\Documents\Projects\GPS Tractor\v1"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For the RTX 3070 Ti, install CUDA PyTorch and SAM2 separately:

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
python -m pip install git+https://github.com/facebookresearch/sam2.git
```

Check CUDA:

```powershell
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

If `.env` does not exist:

```powershell
Copy-Item .env.example .env
```

Then add your Mapbox token.

## Run the UI

```powershell
.\.venv\Scripts\Activate.ps1
python ui_app.py
```

or double-click:

```text
start_ui.bat
```

The UI opens at:

```text
http://127.0.0.1:5000
```

## Farm setup settings

The default overview is deliberately broad:

```text
FARM_OVERVIEW_RADIUS_M=6500
FARM_OVERVIEW_WIDTH=1200
FARM_OVERVIEW_HEIGHT=900
```

This gives roughly a 13 km tall view centred on the farm/postcode, depending on latitude and image dimensions.

Detailed field discovery defaults to:

```text
FIELD_DISCOVERY_MPP=0.8
FIELD_DISCOVERY_IMAGE_SIZE=1000
FIELD_DISCOVERY_TILE_OVERLAP=0.12
FIELD_DISCOVERY_CONTEXT_BUFFER_M=120
FIELD_DISCOVERY_MAX_TILES=180
FIELD_DISCOVERY_PROMPT_SPACING_M=120
```

The context buffer is only used to give fields near the edge of a rough box enough surrounding imagery. SAM prompt points are still restricted to the area the farmer selected.

## Field review

Each detected candidate is shown one at a time. The farmer can:

- keep it and open the boundary editor;
- move/add/delete boundary points;
- reset to the original CV result;
- save the boundary;
- generate the route;
- reject fields that are not theirs.

When a route is generated, `field_routes.py` will fetch terrain once if it is not already saved and internet is available. After terrain/route files exist, route regeneration can use the local data.

## Local farm layout

Typical farm data:

```text
farms/
    example_farm/
        farm.json
        overview.png
        farm_search_area.geojson
        field_discovery.json
        discovery_tiles/
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

## Route status

The planner currently reserves a headland, creates parallel working passes, tries multiple headings and estimates working/turning time. On sufficiently steep terrain, the contour planner still uses **straight passes**, but chooses a heading that better follows equal-elevation direction.

The white lines between passes in `route_overlay.png` are route-order indicators only. They are not yet steering-constrained tractor turns.
