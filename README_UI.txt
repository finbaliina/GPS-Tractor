GPS Tractor local farm UI - first version
========================================

This is the first setup/offline UI layer.

It does NOT replace the existing GPS/CV/terrain/route code. It sits above it.

What this version does
----------------------
- Create farms.
- Import an existing capture as a named farm field.
- Keep the original CV boundary permanently.
- Show the saved satellite image locally.
- Drag boundary vertices.
- Add a vertex to the nearest edge.
- Delete a selected vertex.
- Undo edits.
- Toggle the original CV boundary.
- Reset to original CV result.
- Approve/save the manually corrected boundary.
- List saved fields.
- Open a locally saved route overlay without internet.

Offline design
--------------
All HTML, CSS, JavaScript, satellite imagery and field data are local.
No CDN, Leaflet, Mapbox or internet request is used by this UI.

The browser is only being used as the display for a Python server running on
the same Raspberry Pi / Windows PC.

Install
-------
Copy:
    ui_app.py
    farm_data.py
    templates/
    static/
    start_ui.bat

into the root of your existing v1 project.

Add Flask:
    python -m pip install Flask

and add:
    Flask>=3,<4

to requirements.txt.

Start:
    python ui_app.py

or double-click:
    start_ui.bat

Then open:
    http://127.0.0.1:5000

First workflow
--------------
1. Run your existing main.py once for each field during farm setup.
2. Let CV detect the boundary and generate the capture.
3. Open the UI.
4. Create the farm.
5. Import the capture and give the field a sensible name.
6. Edit/approve the field boundary.
7. Later we will wire "Regenerate route" directly into the UI.

Storage
-------
The UI creates:

farms/
    farm_name/
        farm.json
        fields/
            north_field/
                field.json
                satellite.png
                field_boundary_detected.json
                field_boundary_approved.json
                field_boundary.json
                terrain_data.npz
                terrain.json
                route_plan.json
                route_overlay.png

The original CV boundary is never overwritten.

Important next integration
--------------------------
Saving a manually edited boundary marks the existing route as stale.
The next patch should wire the existing terrain/route code into the UI so
"Approve & save" can regenerate terrain/route immediately.
