GPS TRACTOR - TERRAIN STAGE
===========================

This patch adds terrain.py and updates main.py.

1. Copy terrain.py into the project root.
2. Replace main.py with the supplied main.py.
3. Add `matplotlib` to requirements.txt if it is not already present.
4. Add the settings from env_additions.txt to your actual `.env` file.
5. Install/update dependencies:

   python -m pip install -r requirements.txt

6. Run:

   python main.py

A successful capture folder should now also contain:

   terrain_data.npz
   terrain.json
   terrain_overlay.png

Open terrain_overlay.png first. The contour lines should line up sensibly with
visible hills/valleys and stay inside the detected field boundary.

The existing straight-line route planner is intentionally still used. We should
only make terrain affect routing after the overlay alignment is verified.
