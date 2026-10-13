import tempfile
import unittest
from pathlib import Path

import numpy as np

from pflow_large_store_estimation import pt_tables, score, tvd

HEADER = ("出発地（中ゾーン）,到着地（中ゾーン）,到着施設,目的（細分類）,10歳未満,10～19歳,20～29歳,"
          "30～39歳,40～49歳,50～59歳,60～69歳,70～79歳,80歳以上,合計")


class Units:
    zones = {"5": "名古屋市中村区", "6": "名古屋市中区", "99": "愛知県外・不明"}

    def unit_for_zone(self, zone):
        return self.zones[zone]


class LargeStoreEstimationTest(unittest.TestCase):
    def tables(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pt.csv"
            path.write_text("集計結果\n" + HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
            return pt_tables(path, Units(), ["名古屋市中区", "名古屋市中村区"])

    def test_splits_large_store_shopping_and_keeps_zone5_outside_origins(self):
        od, zone5, outside, ages = self.tables([
            '6,5,大型規模小売店,日常的でない買物,0,0,0,0,0,0,0,0,0,"1,000"',
            "5,5,大型規模小売店,日常的な家事・買物,0,0,0,0,0,0,0,10,0,10",
            "6,6,小型規模小売店,日常的な家事・買物,0,0,0,0,0,0,30,0,0,30",
            "99,5,大型規模小売店,日常的でない買物,0,0,0,0,0,0,7,0,0,7",
            "6,5,大型規模小売店,食事・社交・喫茶,0,0,0,0,0,0,0,0,0,500",
        ])
        np.testing.assert_array_equal(od["large"], [[0, 1000], [0, 10]])
        np.testing.assert_array_equal(od["other"], [[30, 0], [0, 0]])
        np.testing.assert_array_equal(zone5, [1000, 10])
        self.assertEqual(outside, 7)
        self.assertEqual(ages["large"][7], 10)
        self.assertEqual(ages["other"][6], 30)

    def test_score_uses_only_reachable_cells_of_selected_origins(self):
        od = np.array([[3.0, 1.0], [2.0, 2.0]])
        prob = np.array([[0.75, 0.25], [0.5, 0.5]])
        mask = np.array([[True, False], [True, True]])
        result = score(prob, od, np.array([0]), mask)
        self.assertAlmostEqual(result["mean_log_probability"], np.log(0.75))
        self.assertAlmostEqual(result["reachable_share"], 0.75)

    def test_tvd_normalises_counts(self):
        self.assertAlmostEqual(tvd([1, 1], [2, 2]), 0.0)
        self.assertAlmostEqual(tvd([1, 0], [0, 5]), 1.0)


if __name__ == "__main__":
    unittest.main()
