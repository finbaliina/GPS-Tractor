GPS Tractor UI - route generation inside approved boundaries
==========================================================

This patch wires the route planner into the farm setup UI.

Files
-----
field_routes.py
    NEW. Loads the farmer-approved polygon and locally saved terrain, then
    calls the existing route planner.

ui_app.py
    REPLACE. Adds the /route API endpoint.

templates/boundary_editor.html
    REPLACE. Adds Save & generate route and route preview.

static/boundary_editor.js
    REPLACE. Saves the boundary, calls the route planner and refreshes preview.

style_additions.css
    APPEND to the bottom of your existing static/style.css.


How it works
------------
When you press "Save & generate route":

1. The edited polygon is saved as the authoritative boundary.
2. field_routes.py reads that approved polygon.
3. It reads metres_per_pixel from the field's saved metadata.json.
4. It reads terrain_data.npz and terrain.json already stored locally.
5. If p90 slope >= CONTOUR_SLOPE_THRESHOLD_DEG:
       use contour_planning.py
   otherwise:
       use route_planning.py
6. route_plan.json and route_overlay.png are overwritten inside the FIELD
   folder, not the original capture.
7. The new route image appears directly below the boundary editor.

No Mapbox or SAM request is made during route regeneration.

That is important for the eventual offline workflow: once setup data has been
downloaded, route calculation itself can run locally.


Install
-------
Copy field_routes.py into the v1 root.

Replace:
    ui_app.py
    templates/boundary_editor.html
    static/boundary_editor.js

Append style_additions.css to:
    static/style.css

Restart:
    Ctrl+C
    python ui_app.py


Important
---------
This expects imported fields to contain:
    satellite.png
    metadata.json

and preferably:
    terrain_data.npz
    terrain.json

The terrain files are copied when you import one of the existing captures.

If terrain is unavailable, the UI falls back to the normal straight route
planner.

The currently selected steep-field planner remains whichever
contour_planning.py you have installed. With the latest version, this means
straight passes at the best global contour-fitting angle.
