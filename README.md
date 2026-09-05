# RTK Satellite Image Fetcher

A small Raspberry Pi application that:

1. Reads NMEA data from an RTK GNSS receiver.
2. Waits for several consecutive RTK-fixed positions.
3. Uses their median latitude and longitude as the accepted position.
4. Downloads a Mapbox satellite image centred on that position.
5. Saves the image and a JSON metadata file together.

The defaults suit many u-blox ZED-F9P Raspberry Pi HATs, but the serial port,
baud rate and fix requirements can all be changed from the command line.

## Hardware assumptions

- Raspberry Pi 5 running Raspberry Pi OS 64-bit
- RTK HAT outputting NMEA over UART or USB
- An RTK correction source configured separately (for example NTRIP)
- Internet access for Mapbox and, if used, the NTRIP correction stream

This program reads the corrected position produced by the HAT. It does not yet
configure an NTRIP client or send RTCM correction messages to the receiver.

## Raspberry Pi setup

If the HAT uses the Pi UART, enable it with `sudo raspi-config`:

- Interface Options -> Serial Port
- Login shell over serial: **No**
- Serial hardware enabled: **Yes**

Then reboot. The port is normally `/dev/serial0`. A USB receiver will usually
appear as `/dev/ttyACM0` or `/dev/ttyUSB0`.

Install the project:

```bash
cd rtk_satellite
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create a free Mapbox account and access token, then provide it as an environment
variable. Do not put the token directly in the source code.

```bash
export MAPBOX_TOKEN="your_public_mapbox_token"
```

## Run it

Using the default Pi UART:

```bash
python -m rtk_satellite
```

Using a USB receiver:

```bash
python -m rtk_satellite --port /dev/ttyACM0
```

Show the accepted location with a red pin:

```bash
python -m rtk_satellite --marker
```

Accept an RTK-float solution when a fixed solution is unavailable:

```bash
python -m rtk_satellite --allow-float
```

For a test without GNSS hardware:

```bash
python -m rtk_satellite --mock-lat 55.9533 --mock-lon -3.1883
```

Useful options:

```text
--port /dev/serial0       NMEA serial device
--baud 115200             Receiver baud rate
--zoom 18                 Mapbox zoom (roughly neighbourhood/building scale)
--width 1000              Output image width, maximum 1280
--height 1000             Output image height, maximum 1280
--samples 5               Consecutive acceptable positions required
--timeout 300             Maximum seconds to wait for a position
--output-dir captures     Destination directory
--allow-float             Accept NMEA fix quality 5 as well as quality 4
--marker                  Draw a pin on the accepted coordinate
```

Each run creates a timestamped folder such as:

```text
captures/20260905T201530Z/
    satellite.png
    metadata.json
```

The metadata records the coordinate, altitude, fix quality, satellite count,
HDOP, image settings and capture time.

## RTK status

The NMEA GGA fix-quality values used here are:

- `4`: RTK fixed (accepted by default)
- `5`: RTK float (accepted only with `--allow-float`)

An RTK HAT cannot become RTK-fixed from satellite signals alone. It needs RTCM
corrections from a local base station or an NTRIP service. It may still output a
normal GNSS position before those corrections arrive, but this application will
keep waiting rather than treating that as an RTK position.

## Run the tests

```bash
python -m unittest discover -s tests -v
```

