GPS Tractor - fixed-spacing contour patch
============================================

Why this changes the previous approach
--------------------------------------
The previous contour planner used a fixed ELEVATION difference between passes.

That cannot maintain a fixed ground distance where the field slope varies:
- on steep ground, a given elevation change happens over a short distance;
- on shallow ground, the same elevation change happens over a long distance.

That is why large gaps appeared.

This version separates the two jobs:

1. Terrain decides the SHAPE / DIRECTION of the route.
2. Implement width decides the PHYSICAL SPACING of the route.

The planner finds one long representative terrain contour and then creates
parallel geometric offsets from it at:

    IMPLEMENT_WIDTH_M * (1 - OVERLAP)

So for:
    IMPLEMENT_WIDTH_M=6
    OVERLAP=0.02

the target centreline spacing is:

    6 * 0.98 = 5.88 m

The passes are clipped to the working polygon/headland.

Files
-----
contour_planning.py
    Replace your current file with this one.

env_additions.txt
    Add/update these settings in your existing .env.

Run
---
    python main.py

No change to main.py is required if you already installed the previous
contour-planning patch.

Important limitation
--------------------
These are "contour-parallel" passes rather than exact equal-elevation lines.
That distinction is necessary if we want fixed implement-width spacing.

The broad shape follows a real terrain contour, but neighbouring passes are
physical offsets from that shape. This gives complete coverage without large
gaps.

Later we can make each offset gently relax toward the local DEM contour
direction while preserving its target distance from neighbouring passes.
