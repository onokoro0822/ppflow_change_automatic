from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from chukyo_pt_origin_distribution import (
    MiddleZoneInfo,
    build_origin_distributions,
    normalize_zone,
    write_outputs,
    write_charts,
    write_summary_table,
)


def write_pt_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["第6回中京都市圏パーソントリップ調査"])
        writer.writerow(["集計結果"])
        writer.writerow(header)
        writer.writerows(rows)


class ChukyoPtOriginDistributionTest(unittest.TestCase):
    def test_builds_independent_purpose_proxy_distributions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "od.csv"
            header = [
                "出発地（中ゾーン）",
                "到着地（中ゾーン）",
                "目的（細分類）",
                "徒歩",
                "鉄道",
                "合計",
            ]
            write_pt_csv(
                path,
                header,
                [
                    ["1", "5", "日常的な家事・買物", 60, 40, 100],
                    ["2", "5", "食事・社交・喫茶", 20, 30, 50],
                    ["3", "5", "出勤（勤務先へ）", 0, 80, 80],
                    ["4", "5", "観光・行楽・レジャー", 5, 15, 20],
                    ["1", "5", "打合せ・会議・仕事", 2, 8, 10],
                    ["9", "6", "日常的な家事・買物", 999, 0, 999],
                ],
            )
            labels = {
                "1": MiddleZoneInfo(("愛知県",), ("名古屋市中区",), "愛知県 名古屋市中区")
            }

            result = build_origin_distributions(path, ["5.0"], zone_labels=labels)

            self.assertEqual(result["method"], "purpose_proxy")
            self.assertFalse(result["exact_destination_facility_condition"])
            self.assertEqual(result["facilities"]["commercial"]["total_trips"], 170)
            self.assertEqual(result["facilities"]["office"]["total_trips"], 90)
            self.assertEqual(result["facilities"]["hotel"]["total_trips"], 80)
            commercial = result["facilities"]["commercial"]["origin_distribution"]
            self.assertEqual([row["origin_zone"] for row in commercial], ["1", "2", "4"])
            self.assertAlmostEqual(sum(row["share"] for row in commercial), 1.0)
            self.assertEqual(commercial[0]["origin_label"], "愛知県 名古屋市中区")
            self.assertEqual(commercial[0]["mode_counts"], {"徒歩": 60, "鉄道": 40})
            self.assertIn("食事・社交・喫茶", result["overlapping_purposes"])
            self.assertEqual(result["audit"]["mode_total_mismatch_rows"], 0)

    def test_uses_exact_facility_condition_when_column_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "detailed.csv"
            header = [
                "出発地（中ゾーン）",
                "到着地（中ゾーン）",
                "到着施設",
                "目的（細分類）",
                "徒歩",
                "合計",
            ]
            write_pt_csv(
                path,
                header,
                [
                    ["1", "5", "大型規模小売店", "日常的な家事・買物", 100, 100],
                    ["2", "5", "宿泊施設・ホテル", "日常的な家事・買物", 40, 40],
                    ["3", "5", "宿泊施設・ホテル", "観光・行楽・レジャー", 25, 25],
                    ["4", "5", "事務所・会社・銀行・郵便局", "出勤（勤務先へ）", 70, 70],
                ],
            )

            result = build_origin_distributions(path, ["5"])

            self.assertEqual(result["method"], "destination_facility_and_purpose")
            self.assertTrue(result["exact_destination_facility_condition"])
            self.assertEqual(result["limitations"], [])
            self.assertEqual(result["facilities"]["commercial"]["total_trips"], 100)
            self.assertEqual(result["facilities"]["hotel"]["total_trips"], 25)
            self.assertEqual(result["facilities"]["office"]["total_trips"], 70)

    def test_writes_json_and_flat_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "od.csv"
            write_pt_csv(
                source,
                [
                    "出発地（中ゾーン）",
                    "到着地（中ゾーン）",
                    "目的（細分類）",
                    "徒歩",
                    "合計",
                ],
                [["5", "5", "日常的な家事・買物", "1,200", "1,200"]],
            )
            result = build_origin_distributions(source, ["5"])

            json_path, csv_path = write_outputs(result, directory / "output")

            saved = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["facilities"]["commercial"]["total_trips"], 1200)
            with csv_path.open(encoding="utf-8-sig", newline="") as input_file:
                rows = list(csv.DictReader(input_file))
            self.assertEqual(rows[0]["origin_zone"], "5")
            self.assertEqual(rows[0]["is_intrazonal"], "true")

    def test_writes_summary_table_and_charts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "od.csv"
            write_pt_csv(
                source,
                [
                    "出発地（中ゾーン）",
                    "到着地（中ゾーン）",
                    "目的（細分類）",
                    "徒歩",
                    "合計",
                ],
                [
                    ["1", "5", "日常的な家事・買物", 100, 100],
                    ["2", "5", "出勤（勤務先へ）", 80, 80],
                    ["3", "5", "観光・行楽・レジャー", 40, 40],
                ],
            )
            result = build_origin_distributions(source, ["5"])
            output_dir = directory / "output"

            table_path = write_summary_table(result, output_dir, top_n=2)
            chart_paths = write_charts(result, output_dir, top_n=2)

            self.assertTrue(table_path.exists())
            self.assertEqual(len(chart_paths), 8)
            self.assertTrue(all(path.exists() and path.stat().st_size > 100 for path in chart_paths))
            self.assertEqual({path.suffix for path in chart_paths}, {".png", ".svg"})

    def test_rejects_missing_header_and_normalizes_zone(self) -> None:
        self.assertEqual(normalize_zone("5.0"), "5")
        self.assertEqual(normalize_zone(5), "5")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.csv"
            path.write_text("not,a,pt,table\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_origin_distributions(path, ["5"])


if __name__ == "__main__":
    unittest.main()
