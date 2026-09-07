GPS Tractor UI - field editor 500 error fix
==============================================

Replace:
    farm_data.py

with the supplied version.

Likely cause
------------
The first UI assumed field_boundary.json used one of only a few JSON layouts.
The existing GPS Tractor CV code has used different boundary key names during
development, so opening a field could fail while trying to extract its polygon.

The new parser understands:
    points
    polygon_pixels
    boundary_pixels
    coordinates
    polygon
    geometry / GeoJSON Polygon
    Feature
    FeatureCollection
    raw coordinate lists

It also repairs fields imported with the previous UI:
- if field_boundary_detected.json is missing, it creates it from the current
  boundary;
- if field_boundary_approved.json is missing or unreadable, it rebuilds it.

After replacing the file:
    stop ui_app.py
    python ui_app.py

Then try Edit boundary again.

If there is still a 500 error, copy the traceback printed in the PowerShell
window; that will identify the exact failing line.
