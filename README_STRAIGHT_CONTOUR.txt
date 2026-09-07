GPS Tractor - straight contour planner
======================================

This replaces the curved contour route planner.

The new rule is simple:

    ALL working passes are straight.

The terrain DEM is still used, but only to decide which straight-line heading
best matches the field's equal-elevation / contour direction.

How it works
------------
For every candidate angle (0-179 degrees):

1. Calculate how closely that straight heading matches the local terrain
   contour direction across the working polygon.
2. Generate actual straight, implement-width-spaced passes.
3. Calculate route time / turns.
4. Score terrain alignment and route efficiency together.
5. Choose the best angle.

So a complicated terrain map may have many curved contour lines, but the
tractor gets one sensible straight working direction which is the best global
fit to those contours.

This is likely much closer to normal practical field working than asking the
operator to follow large artificial curves.

Install
-------
Replace:
    contour_planning.py

Add/update the values in env_additions.txt in your ACTUAL .env.

No change to main.py is required if it already switches to
plan_contour_route() for steep fields.

Suggested initial settings
--------------------------
CONTOUR_STRAIGHT_ANGLE_STEP_DEG=1.0
CONTOUR_ALIGNMENT_WEIGHT=8.0
CONTOUR_DIRECTION_MIN_SLOPE_DEG=1.0
CONTOUR_EXTRA_OVERLAP=0.08

Expected console output
-----------------------
Straight contour route: 43 passes, 42 turns, 37.0°,
mean contour-direction error 8.6°,
99.7% working-area coverage, about 31.2 min
