from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from chukyo_pt_facility_time_distribution import (
    build_time_distributions,
    largest_remainder,
    parse_time_column,
    write_outputs,
)


def write_time_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["条件設定"])
        writer.writerow(["粒度", "中ゾーン"])
        writer.writerow(["集計結果"])
        writer.writerow(
            [
                "集計種類",
                "到着施設",
                "目的（細分類）",
                "３時",
                "10時",
                "27時以降",
                "不明",
                "合計",
            ]
        )
        writer.writerows(
            [
                [
                    "5",
                    "大型規模小売店",
                    "日常的な家事・買物",
                    10,
                    20,
                    0,
                    2,
                    32,
                ],
                [
                    "5",
                    "大型規模小売店",
                    "出勤（勤務先へ）",
                    999,
                    999,
                    0,
                    0,
                    1998,
                ],
                [
                    "5",
                    "事務所・会社・銀行・郵便局",
                    "出勤（勤務先へ）",
                    2,
                    18,
                    0,
                    0,
                    20,
                ],
                [
                    "5",
                    "宿泊施設・ホテル",
                    "観光・行楽・レジャー",
                    1,
                    2,
                    7,
                    0,
                    10,
                ],
                [
                    "6",
                    "大型規模小売店",
                    "日常的な家事・買物",
                    500,
                    500,
                    0,
                    0,
                    1000,
                ],
            ]
        )


class ChukyoPtFacilityTimeDistributionTest(unittest.TestCase):
    def test_parses_fullwidth_and_late_hour_columns(self) -> None:
        self.assertEqual(parse_time_column("３時"), 3)
        self.assertEqual(parse_time_column("27時以降"), 27)
        self.assertIsNone(parse_time_column("不明"))

    def test_builds_exact_facility_arrival_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "time.csv"
            write_time_csv(source)
            result = build_time_distributions(
                source, "5.0", target_visitors=7
            )

            self.assertEqual(
                result["scope"]["time_dimension"],
                "移動終了時（到着時間帯）",
            )
            commercial = result["facilities"]["commercial"]
            self.assertEqual(commercial["known_time_trips"], 30)
            self.assertEqual(commercial["unknown_time_trips"], 2)
            self.assertEqual(
                [
                    row["trip_count"]
                    for row in commercial["hourly_distribution"]
                ],
                [10, 20, 0],
            )
            self.assertEqual(
                [
                    row["projected_target_visitors"]
                    for row in commercial["hourly_distribution"]
                ],
                [2, 5, 0],
            )
            self.assertEqual(
                result["facilities"]["office"]["known_time_trips"], 20
            )
            self.assertEqual(
                result["facilities"]["hotel"]["known_time_trips"], 10
            )
            self.assertEqual(
                result["audit"]["selected_row_time_total_mismatch_rows"], 0
            )

    def test_largest_remainder_preserves_total(self) -> None:
        self.assertEqual(largest_remainder(10, [1, 1, 1]), [4, 3, 3])
        self.assertEqual(
            sum(largest_remainder(25_284, [3, 7, 11])), 25_284
        )

    def test_writes_json_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "time.csv"
            write_time_csv(source)
            result = build_time_distributions(
                source, "5", target_visitors=7
            )
            json_path, csv_path = write_outputs(
                result, directory / "output"
            )

            saved = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["audit"]["commercial_projected_total"], 7
            )
            with csv_path.open(
                encoding="utf-8-sig", newline=""
            ) as input_file:
                rows = list(csv.DictReader(input_file))
            self.assertEqual(len(rows), 9)
            self.assertEqual(rows[0]["projected_target_visitors"], "2")


if __name__ == "__main__":
    unittest.main()
