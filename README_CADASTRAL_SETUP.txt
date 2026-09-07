GPS Tractor - cadastral farm setup first draft
==============================================

What this adds
--------------
1. Home screen now starts farm creation via "Set up new farm".
2. Farmer enters farm name + postcode.
3. Mapbox geocoding gets a rough farm location.
4. Local cached RoS/HMLR cadastral polygons near that location are loaded.
5. A broad Mapbox satellite overview is shown with candidate parcels overlaid.
6. The farmer clicks the parcels they farm.
7. The selected cadastral geometry is saved locally.
8. A buffered farm search area is saved locally (default +800 m).
9. The farmer is sent to the existing farm page and can add/review fields.
10. Registry data is not required again after setup.

This is deliberately the first draft of the setup flow. It does NOT yet automatically
run SAM across the whole saved farm search area and create every field. The existing
single-field capture/import flow remains available, so fields are addable later exactly
as requested. The next stage is to connect farm_search_area.geojson to tiled high-res
Mapbox imagery + multi-field SAM discovery.

Files to copy/replace
---------------------
New:
  cadastral_lookup.py
  farm_setup.py
  templates/setup_search.html
  templates/setup_parcels.html
  static/parcel_selector.js
  cadastral_data/README.txt

Replace:
  ui_app.py
  farm_data.py
  templates/home.html
  templates/farm.html
  static/style.css

Dependencies
------------
python -m pip install Flask geopandas pyogrio

The existing project already uses requests/shapely.

Test without downloading registry data
--------------------------------------
Add this to .env:
  CADASTRAL_DEMO=true

Then:
  python ui_app.py

Click "Set up new farm", enter a real farm name/postcode, and the Mapbox overview
will show synthetic cadastral rectangles around the geocoded location. This tests the
selection and save flow only.

Real cadastral data
-------------------
Place official RoS/HMLR data under cadastral_data/. The loader recursively accepts:
.gpkg, .shp, .geojson, .json and .gml and reprojects to/from EPSG:27700 as needed.

Useful .env settings
--------------------
FARM_LOOKUP_RADIUS_M=4500
FARM_EXTRA_VIEW_BUFFER_M=800
FARM_OVERVIEW_WIDTH=1200
FARM_OVERVIEW_HEIGHT=900
CADASTRAL_DEMO=false

Saved setup data
----------------
farms/<farm>/
  farm.json
  overview.png
  cadastral_selection.geojson
  farm_search_area.geojson
  fields/

The +800 m farm_search_area is specifically there so land missing from the cadastral
selection (for example rented fields) is still in view during the later field-discovery
step.
