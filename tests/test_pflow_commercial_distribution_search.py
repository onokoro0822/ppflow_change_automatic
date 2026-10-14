import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pflow_commercial_distribution_search as search


def row(addition, arrivals=3000, tvd=.2, target=3000):
    return dict(target=target, mesh_addition=addition,
                arrivals_full_equivalent=arrivals, tvd_aichi=tvd,
                capacity=10000, scenario="fixture.json")


class CommercialSearchTests(unittest.TestCase):
    def test_count_constraint_overrides_better_distribution(self):
        best = search.select_candidates([row(1000, 6000, .01), row(2000, 3000, .4)],
                                        [3000], .1, .15)
        self.assertEqual(best["3000"]["mesh_addition"], 2000)
        self.assertFalse(best["3000"]["joint_met"])

    def test_no_feasible_result_is_explicit(self):
        best = search.select_candidates([row(1000, 2000, .01)], [3000, 6000], .1, .15)
        self.assertEqual(best, {"3000": None, "6000": None})

    def test_undefined_distribution_is_not_selected(self):
        best = search.select_candidates([row(1000, tvd=float("nan")),
                                         row(2000, tvd=.1)], [3000], .1, .15)
        self.assertEqual(best["3000"]["mesh_addition"], 2000)
        self.assertTrue(best["3000"]["joint_met"])

    def test_refinement_uses_both_neighbors(self):
        points = search.refinement_points([row(1000, tvd=.3), row(3000, tvd=.1),
                                           row(6000, tvd=.4)], [3000], {1000, 3000, 6000}, .1, .15)
        self.assertEqual(points, [2000, 4500])

    def test_no_feasible_fit_refines_nearest_count(self):
        points = search.refinement_points([row(1000, 1000), row(3000, 2500),
                                           row(6000, 5000)], [3000], {1000, 3000, 6000}, .1, .15)
        self.assertEqual(points, [2000, 4500])

    def test_multi_stage_orchestration_and_verifier_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pt = root / "pt.csv"
            pt.write_text("fixture")
            out = root / "output"
            commands = []
            def fake_run(command, check):
                commands.append(command)
                batch_out = Path(command[command.index("--output") + 1])
                start = command.index("--mesh-additions") + 1
                stop = command.index("--rounds")
                adds = list(map(float, command[start:stop]))
                batch_out.mkdir()
                batch = dict(status="finished", trials=[row(a, tvd=abs(a-2250)/10000) for a in adds])
                for key in ("pt_raw_counts", "pt_aichi_normalized", "pt_aichi_class_shares"):
                    batch[key] = {}
                batch.update(pt_csv=str(pt), pt_sha256="fixture", pflow_args="fixture", competing_capacity=3220000)
                (batch_out / "report.json").write_text(json.dumps(batch))
            with patch.object(search.subprocess, "run", side_effect=fake_run):
                search.main(["--pt-csv", str(pt), "--output", str(out), "--baseline", str(root),
                             "--targets", "3000", "--mesh-additions", "1000", "3000", "6000"])
            self.assertEqual(len(commands), 2)
            result = json.loads((out / "selected_pilot.json").read_text())
            self.assertEqual(result["best"]["3000"]["mesh_addition"], 2000)
            self.assertEqual(len(result["trials"]), 5)
            # Reference is rescaled to the first increment of each batch.
            ceiling = float(commands[1][commands[1].index("--reference-ceiling") + 1])
            self.assertAlmostEqual(ceiling, 122 * (22230+2000)/(22230+745.942954248366))

    def test_failed_batch_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pt = root / "pt.csv"
            pt.write_text("fixture")
            out = root / "output"
            with patch.object(search.subprocess, "run", side_effect=RuntimeError("fixture failure")):
                with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                    search.main(["--pt-csv", str(pt), "--output", str(out), "--baseline", str(root)])
            result = json.loads((out / "report.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertFalse((out / "selected_pilot.json").exists())

    def test_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pt = root / "pt.csv"
            pt.write_text("fixture")
            with self.assertRaises(SystemExit), patch.object(search.subprocess, "run") as run:
                search.main(["--pt-csv", str(pt), "--output", str(root), "--baseline", str(root)])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
