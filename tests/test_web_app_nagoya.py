from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_prototype import ScenarioRule
from web_app import INDEX_HTML, apply_streaming_nagoya, geocode_payload, nagoya_input_files


class NagoyaStreamingTest(unittest.TestCase):
    def make_inputs(self, directory: Path) -> list[Path]:
        paths = []
        for ward in range(1, 17):
            path = directory / f"trip_231{ward:02d}.csv"
            path.write_text(
                f"person-{ward},36000,136.89,35.17,136.90,35.17,1,100,21\n",
                encoding="utf-8",
            )
            paths.append(path)
        return paths

    def test_all_16_files_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            self.make_inputs(input_dir)
            with patch("web_app.NAGOYA_INPUT_DIR", input_dir):
                self.assertEqual(len(nagoya_input_files()), 16)

    def test_dialogue_and_simulation_are_separate_ui_steps(self) -> None:
        self.assertIn("シミュレーション入力（別工程）", INDEX_HTML)
        self.assertIn("計画条件を人流解析へ反映", INDEX_HTML)
        self.assertIn("上の対話はトリップデータを参照しません", INDEX_HTML)
        self.assertIn('/api/geocode', INDEX_HTML)

    def test_geocode_payload_returns_coordinates_for_target_label(self) -> None:
        result = {
            "label": "名鉄名古屋駅, 名古屋市, 日本",
            "lon": 136.8845,
            "lat": 35.1708,
        }
        with patch("web_app.geocode_place", return_value=result) as geocode:
            payload = geocode_payload("名鉄百貨店本店")

        geocode.assert_called_once()
        self.assertEqual(geocode.call_args.args[0], "名鉄名古屋駅")
        self.assertEqual(payload["target_label"], "名鉄百貨店本店")
        self.assertEqual(payload["target_lon"], 136.8845)
        self.assertEqual(payload["target_lat"], 35.1708)

    def test_streaming_pipeline_scans_every_ward_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            paths = self.make_inputs(input_dir)
            rule = ScenarioRule(
                scenario_text="名古屋駅前にショッピングモールを整備する。",
                scenario_name="test",
                target_label="名古屋駅",
                target_lon=136.895,
                target_lat=35.17,
                affected_ratio=1.0,
                affected_purposes=["100"],
                time_window=None,
                strength=0.28,
                influence_radius_km=5.0,
                random_seed=42,
                questions=[],
                notes=[],
            )
            args = SimpleNamespace(sample_lines=20, background_points=20)
            changed_csv = input_dir / "changed.csv"

            with patch("web_app.nagoya_input_files", return_value=paths):
                baseline, scenario, candidates, changed, summary = apply_streaming_nagoya(
                    rule,
                    args,
                    changed_csv,
                )

            self.assertEqual(summary["total_trips"], 16)
            self.assertEqual(summary["candidate_trips"], 16)
            self.assertEqual(summary["changed_trips"], 16)
            self.assertEqual(len(changed), 16)
            self.assertEqual(len(baseline), len(scenario))
            self.assertEqual(len(candidates), len(baseline))
            self.assertEqual(len(changed_csv.read_text(encoding="utf-8").splitlines()), 17)


if __name__ == "__main__":
    unittest.main()
