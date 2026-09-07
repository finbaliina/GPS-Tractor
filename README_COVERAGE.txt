GPS Tractor - contour coverage/smoothing patch
===============================================

Replace:
    contour_planning.py

Add/update the settings in env_additions.txt in your ACTUAL .env.

What changed
------------
1. Smoothing no longer "solves" kinks by accepting gaps.
   After smoothing, the program calculates actual implement coverage.

2. If coverage is below the target, it reduces the distance between passes
   and tries again. That means extra overlap, never deliberately wider gaps.

3. The reference terrain contour is extended several field widths beyond both
   ends BEFORE offsets are generated. This addresses the wedge-shaped missed
   areas that appeared at the bottom-left and top-right of the test field.

4. Coverage is measured using the real implement width:
       each centreline is buffered by IMPLEMENT_WIDTH_M / 2
   and the union of those worked strips is compared with the working polygon.

Example
-------
IMPLEMENT_WIDTH_M=6
OVERLAP=0.02
CONTOUR_EXTRA_OVERLAP=0.08

Initial target spacing:
    6 * (1 - 0.10) = 5.40 m

If the smoothed geometry only covers 98.9%, the planner might retry at:
    5.40 * 0.95 = 5.13 m
then:
    5.13 * 0.95 = 4.87 m

until it reaches the requested coverage target or the iteration limit.

Run
---
    python main.py

Look for console output such as:
    Contour coverage test 1: 98.72% coverage at 5.40 m spacing
    Contour coverage test 2: 99.63% coverage at 5.13 m spacing
    Contour route: ... 99.63% working-area coverage
