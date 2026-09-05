# GPS Tractor — RTK Satellite Image Fetcher

This Python project is designed for a Raspberry Pi 5 with an RTK GNSS HAT. It:

1. Reads NMEA GGA messages from the receiver.
2. Waits for several consecutive RTK-fixed positions.
3. Uses the median coordinate to reduce single-reading noise.
4. Downloads a Mapbox satellite image centred on that coordinate.
5. Saves the image and a JSON metadata file.
6. Opens a simple window for the operator to check the image and name the area.
7. Maintains a local count of successful Mapbox image requests.

You can test the complete image and review flow on Windows using mock coordinates,
without connecting the RTK hardware.

## What is included

```text
rtk_satellite/          Python application package
tests/                  Automated tests
.gitignore              Keeps tokens, captures and virtual environments out of Git
requirements.txt        Python dependencies
pyproject.toml          Package information
README.md               This guide
```

The program creates `captures/` and `.mapbox_usage.json` when it runs. Both are
ignored by Git. The access token is read from an environment variable and is not
stored in this project.

## Windows setup and mock test

Open PowerShell and change to the folder containing this README. Quotation marks
are important because `GPS Tractor` contains a space:

```powershell
cd "C:\Users\Finlay\Documents\Projects\GPS Tractor\v1"
```

Create a virtual environment. This only needs to be done once:

```powershell
py -m venv .venv
```

Activate it whenever you open a new PowerShell window:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks that script, allow it only for the current PowerShell
window, then try activation again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

Set your Mapbox public token for the current PowerShell window. Replace the
example with the token beginning `pk.` from your Mapbox account:

```powershell
$env:MAPBOX_TOKEN="pk.your_token_here"
```

Run a complete test using a coordinate in Edinburgh:

```powershell
python -m rtk_satellite --mock-lat 55.9486 --mock-lon -3.1999 --marker
```

The image should download and then appear in a review window. Enter an area name
and choose **Confirm and save**, or choose **Image is wrong**. Other useful mock
locations include:

```powershell
python -m rtk_satellite --mock-lat 51.5074 --mock-lon -0.1278 --marker
python -m rtk_satellite --mock-lat 57.1497 --mock-lon -2.0943 --marker
```

To test downloading without opening the window:

```powershell
python -m rtk_satellite --mock-lat 55.9486 --mock-lon -3.1999 --no-review
```

## Raspberry Pi 5 setup

Use Raspberry Pi OS 64-bit. In a terminal, install virtual-environment and UI
support:

```bash
sudo apt update
sudo apt install python3-venv python3-tk
```

From the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export MAPBOX_TOKEN="pk.your_token_here"
```

If the HAT uses the Pi UART, run `sudo raspi-config`, then select:

- **Interface Options → Serial Port**
- Login shell over serial: **No**
- Serial hardware enabled: **Yes**

Reboot afterwards. The UART is normally `/dev/serial0`. A USB receiver will
usually appear as `/dev/ttyACM0` or `/dev/ttyUSB0`.

Run with the default Pi UART:

```bash
python -m rtk_satellite --marker
```

Or specify a USB serial device:

```bash
python -m rtk_satellite --port /dev/ttyACM0 --marker
```

## RTK correction requirement

The program reads corrected positions produced by the HAT, but it does not yet
connect to an NTRIP caster or feed RTCM corrections to the receiver. The receiver
must already be receiving corrections from an NTRIP service or local base station.

NMEA GGA fix qualities used by the program are:

- `4`: RTK fixed — accepted by default
- `5`: RTK float — accepted only when `--allow-float` is used

By default, five consecutive RTK-fixed readings are required. The program waits
up to 300 seconds.

## Common options

| Option | Meaning |
|---|---|
| `--port /dev/serial0` | NMEA serial device |
| `--baud 115200` | Receiver baud rate |
| `--samples 5` | Consecutive acceptable positions required |
| `--timeout 300` | Maximum time to wait for a position |
| `--allow-float` | Also accept RTK-float quality |
| `--zoom 18` | Mapbox zoom level |
| `--width 1000` | Image width, up to 1280 pixels |
| `--height 1000` | Image height, up to 1280 pixels |
| `--marker` | Draw a red marker at the coordinate |
| `--no-review` | Save without opening the review window |
| `--output-dir captures` | Change the capture destination |

Run `python -m rtk_satellite --help` to see the command-line help.

## Saved results

Every successful run creates a timestamped directory:

```text
captures/20260905T201530Z/
    satellite.png
    metadata.json
```

The metadata contains the coordinate, positioning source, fix information, image
settings and creation time. After review it also contains whether the operator
confirmed the image and the chosen area name.

## Mapbox request counter

One successful image download normally makes one Mapbox Static Images API request;
it does not consume a separate object called a “token.” The token identifies and
authorizes your account. The program increments `.mapbox_usage.json` after each
successful download and resets its local count when the UTC month changes.

This count only knows about requests made by this copy of the project. It cannot
see requests from other computers, deleted usage files, failed requests that may
have reached Mapbox, or account-wide billing. Mapbox's dashboard is authoritative
and may not update immediately. The displayed 50,000-request allowance is an
estimate configured in `rtk_satellite/usage.py`; check your current Mapbox plan
before relying on it.

## Automated tests

With the virtual environment activated:

```powershell
python -m unittest discover -s tests -v
```

The tests do not use your Mapbox token, make network requests or need GNSS hardware.

## Upload changes to GitHub

After copying these files into your Git repository and testing them:

```powershell
git status
git add .
git commit -m "Add complete RTK satellite capture and review app"
git push
```

Because `.gitignore` excludes `.venv`, captures, usage data and `.env`, those
machine-specific or private files should not be uploaded. Always inspect
`git status` before committing.
