from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from facility_preference import build_facility_preference


def write_profile(path: Path, total: int, category_counts: dict[str, int]) -> None:
    path.write_text(
        json.dumps(
            {
                "scope": {"type": "test"},
                "counts": {"selected_trips": total, "unique_persons": total},
                "distributions": {
                    "employment_status": [
                        {
                            "category": category,
                            "count": count,
                            "share": count / total,
                        }
                        for category, count in category_counts.items()
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


class FacilityPreferenceTest(unittest.TestCase):
    def test_calculates_relative_composition_and_sample_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            baseline = directory / "baseline.json"
            facility = directory / "facility.json"
            write_profile(baseline, 1_000, {"employed": 500, "student": 500})
            write_profile(facility, 100, {"employed": 70, "student": 30})

            result = build_facility_preference(
                baseline,
                facility,
                directory / "output",
                dimensions=("employment_status",),
                minimum_facility_count=40,
            )

            comparisons = {
                row["category"]: row for row in result["comparisons"]
            }
            self.assertAlmostEqual(
                comparisons["employed"]["relative_preference"], 1.4
            )
            self.assertEqual(
                comparisons["employed"]["interpretation"], "overrepresented"
            )
            self.assertEqual(
                comparisons["student"]["interpretation"], "insufficient_sample"
            )
            self.assertTrue((directory / "output" / "preference.json").exists())
            self.assertTrue((directory / "output" / "preference.csv").exists())

    def test_rejects_empty_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            baseline = directory / "baseline.json"
            facility = directory / "facility.json"
            write_profile(baseline, 1_000, {"employed": 1_000})
            write_profile(facility, 0, {})

            with self.assertRaises(ValueError):
                build_facility_preference(
                    baseline,
                    facility,
                    directory / "output",
                    dimensions=("employment_status",),
                )


if __name__ == "__main__":
    unittest.main()
