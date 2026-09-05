from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rtk_satellite.review import save_review_metadata


class ReviewMetadataTests(unittest.TestCase):
    def test_saves_confirmed_area_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            metadata_path = Path(temporary_directory) / "metadata.json"
            metadata_path.write_text('{"position": {}}\n', encoding="utf-8")

            save_review_metadata(metadata_path, True, "  North Field  ")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            self.assertTrue(metadata["review"]["confirmed"])
            self.assertEqual(metadata["review"]["area_name"], "North Field")
            self.assertIn("reviewed_at", metadata["review"])

    def test_saves_rejection_without_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            metadata_path = Path(temporary_directory) / "metadata.json"
            metadata_path.write_text('{"position": {}}\n', encoding="utf-8")

            save_review_metadata(metadata_path, False, None)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            self.assertFalse(metadata["review"]["confirmed"])
            self.assertIsNone(metadata["review"]["area_name"])


if __name__ == "__main__":
    unittest.main()
