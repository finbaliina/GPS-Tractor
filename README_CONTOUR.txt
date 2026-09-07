GPS Tractor - contour route patch
=================================

Files
-----
terrain.py
    Replacement for the previous terrain.py. Adds image_x, image_y and
    slope_grid_deg to TerrainData so the contour planner can work in exactly
    the same pixel coordinate system as the satellite image.

contour_planning.py
    New contour-following route planner.

main.py
    Replacement main.py. Automatically chooses contour mode when the
    field's 90th-percentile slope is >= CONTOUR_SLOPE_THRESHOLD_DEG.

route_visualisation.py
    Replacement visualiser. Works with both the normal RoutePlan and
    ContourRoutePlan, and safely draws MultiPolygons.

env_additions.txt
    Settings to add to your existing .env.


Install
-------
No new Python package is required beyond the terrain patch:
numpy, opencv-python, matplotlib, shapely are already used.

Copy the four .py files into your v1 folder, replacing the files of the same
name. Add the env settings to your ACTUAL .env file.

Run:
    python main.py


What the first contour planner does
-----------------------------------
1. Keeps the normal headland by buffering the field inward.
2. Smooths the DEM slightly.
3. Uses the field median slope and implement width to choose an elevation
   interval:
       vertical_step ~= implement_width * tan(slope)
4. Generates iso-elevation curves at that interval.
5. Clips those curves to the working polygon.
6. Orders the resulting curved passes into a practical back-and-forth sequence.

This means the passes really do follow equal-elevation curves.

Important limitation
--------------------
This is the first working contour planner. The pass spacing is approximately
one implement width at the representative (median) slope. If slope changes a
lot across the field, local ground spacing will vary somewhat.

The white connectors are also still only route-order indicators; they are not
yet physically driveable turns. Those two things are the next refinements after
we inspect the output.
