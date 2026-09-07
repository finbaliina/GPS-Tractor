# GPS Tractor

The main program is deliberately split into one file per step:

```text
main.py                 Calls each step in order
gps_location.py         Gets mock or RTK GPS position
satellite_image.py      Downloads Mapbox image and records image scale
image_review.py         Lets the operator confirm/name the image
field_boundary.py       Runs SAM and draws the detected field boundary
route_planning.py       Chooses the parallel coverage route
route_visualisation.py  Draws the route over the satellite image
tractor_profiles.json   Tractor geometry
.env                    User/machine settings (not committed)
```

`main.py` should stay small. If a step changes later — for example SAM moves
from the local PC to AWS — only that step's file should need replacing.

## Current process

```text
GPS position
    ↓
Mapbox satellite image
    ↓
operator confirms image
    ↓
SAM field boundary
    ↓
route optimisation
    ↓
route drawn over image
```

Every run writes its outputs to one timestamped folder under `captures/`.

## Windows development setup

This project deliberately does not include `.venv`.

Create it:

```powershell
cd "C:\Users\Finlay\Documents\Projects\GPS Tractor\v1"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For the RTX 3070 Ti, install CUDA PyTorch before SAM:

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
python -m pip install git+https://github.com/facebookresearch/sam2.git
```

Check the GPU:

```powershell
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

Copy `.env.example` to `.env`, add the Mapbox token, then run:

```text
start.bat
```

## Configuration

Normal values live in `.env`; tractor geometry lives in `tractor_profiles.json`.

While the GPS receiver is not fitted, leave:

```text
GPS_MODE=mock
```

The satellite image defaults to zoom 14 so that whole UK fields are more likely
to fit in the image.

## Route-planning status

The planner currently:
- reserves a headland;
- generates parallel passes at the implement width;
- tries orientations from 0–180 degrees;
- estimates the cost of turns using tractor turning radius;
- chooses the lowest estimated-time route.

The white connections drawn between passes currently show **route order only**.
They are not yet physically accurate turning paths. Wheelbase, steering angle,
minimum turning radius and reverse capability are already stored in
`tractor_profiles.json` ready for the next stage.
