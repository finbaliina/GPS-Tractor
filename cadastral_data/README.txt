GPS Tractor cadastral data cache
================================

Put official cadastral parcel files in this folder or subfolders.
Supported formats: .gpkg, .shp, .geojson, .json, .gml

Suggested layout:

cadastral_data/
  scotland_ros/
    ...RoS INSPIRE cadastral parcel files...
  england_wales_hmlr/
    ...HMLR INSPIRE Index Polygon files...

The first-draft UI reads these files only during initial farm setup.
Once the farmer confirms their parcels, the selected geometry is copied into:

farms/<farm>/cadastral_selection.geojson
farms/<farm>/farm_search_area.geojson

Normal field review, route generation and tractor use do not query the registry.

For UI testing without downloading registry data, add to .env:
CADASTRAL_DEMO=true

Demo geometry is synthetic and is clearly labelled as demo data.
