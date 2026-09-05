from __future__ import annotations

import unittest

from rtk_satellite.gnss import GgaReading, combine_readings, parse_gga
from rtk_satellite.mapbox import ImageSettings, build_static_image_url


class GnssTests(unittest.TestCase):
    def test_parses_rtk_fixed_gga(self) -> None:
        line = "$GNGGA,123519.00,5557.19800,N,00311.29800,W,4,20,0.7,80.0,M,50.0,M,1.0,0000"
        reading = parse_gga(line)
        self.assertIsNotNone(reading)
        assert reading is not None
        self.assertEqual(reading.fix_quality, 4)
        self.assertEqual(reading.satellites, 20)
        self.assertAlmostEqual(reading.latitude, 55.9533, places=4)
        self.assertAlmostEqual(reading.longitude, -3.1883, places=4)

    def test_non_gga_is_ignored(self) -> None:
        self.assertIsNone(parse_gga("$GNRMC,123519.00,A,5557.198,N,00311.298,W"))

    def test_combines_with_median(self) -> None:
        readings = [
            GgaReading(55.0, -3.0, 100.0, 4, 20, 0.8),
            GgaReading(55.2, -3.2, 102.0, 4, 21, 0.6),
            GgaReading(99.0, -99.0, 101.0, 4, 19, 0.7),
        ]
        position = combine_readings(readings)
        self.assertEqual(position.latitude, 55.2)
        self.assertEqual(position.longitude, -3.2)
        self.assertEqual(position.altitude_m, 101.0)
        self.assertEqual(position.samples, 3)


class MapboxTests(unittest.TestCase):
    def test_builds_static_url_without_exposing_unescaped_token(self) -> None:
        settings = ImageSettings(zoom=18, width=800, height=600, marker=True)
        url = build_static_image_url(55.9533, -3.1883, "token with spaces", settings)
        self.assertIn("satellite-v9/static/pin-s+e63946", url)
        self.assertIn("-3.18830000,55.95330000,18,0/800x600", url)
        self.assertIn("token%20with%20spaces", url)

    def test_rejects_oversize_image(self) -> None:
        with self.assertRaises(ValueError):
            build_static_image_url(55, -3, "token", ImageSettings(width=1281))


if __name__ == "__main__":
    unittest.main()
