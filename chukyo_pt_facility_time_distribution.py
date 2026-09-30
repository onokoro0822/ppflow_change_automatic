"""中京PTから中ゾーン別・施設用途別の到着時間分布を作成する。

入力の「移動終了時」は到着した時刻を表す。名鉄百貨店跡地を含む中ゾーン5について、
到着施設と目的（細分類）を同時に限定し、商業・オフィス・ホテルの比較図と、
名鉄跡地の想定来訪者数へ按分した商業到着人数図を出力する。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from chukyo_pt_origin_distribution import FACILITY_USES, FacilityUse, parse_count, sha256_file


PROJECT_DIR = Path(__file__).resolve().parent
PT_DATA_DIR = PROJECT_DIR.parent / "中京PTデータ"
DEFAULT_INPUT = PT_DATA_DIR / "chukyo_pt_2022_destination_facility_purpose_time.csv"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "chukyo_pt_facility_time_distribution"
DEFAULT_DESTINATION_ZONE = "5"
DEFAULT_MEITETSU_VISITORS = 25_284

FACILITY_COLUMN = "到着施設"
PURPOSE_COLUMN = "目的（細分類）"
TOTAL_COLUMN = "合計"
UNKNOWN_COLUMN = "不明"
REQUIRED_COLUMNS = {FACILITY_COLUMN, PURPOSE_COLUMN, TOTAL_COLUMN}


def normalize_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalize_zone(value: object) -> str:
    text = normalize_text(value)
    if re.fullmatch(r"[+-]?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def parse_time_column(name: str) -> int | None:
    """PTの時間列名（例: ３時、10時、27時以降）を開始時刻へ変換する。"""

    match = re.fullmatch(r"(\d{1,2})時(?:以降)?", normalize_text(name))
    return int(match.group(1)) if match else None


def read_time_table(path: Path) -> tuple[list[dict[str, str]], tuple[str, ...], int]:
    """メタデータブロックの下にある実表を読み込む。"""

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        for line_no, row in enumerate(reader, start=1):
            header = tuple(cell.strip() for cell in row)
            if REQUIRED_COLUMNS.issubset(header) and any(
                parse_time_column(column) is not None for column in header
            ):
                break
        else:
            raise ValueError(f"時間帯別PT表のヘッダーが見つかりません: {path}")

        rows: list[dict[str, str]] = []
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) < len(header):
                row.extend([""] * (len(header) - len(row)))
            rows.append(
                {name: row[index].strip() for index, name in enumerate(header)}
                | {"__line__": str(reader.line_num)}
            )
    return rows, header, line_no


def largest_remainder(total: int, weights: Sequence[float]) -> list[int]:
    """構成比を保ちながら整数人数へ按分する。"""

    if total < 0:
        raise ValueError("total must be non-negative")
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return [0] * len(weights)
    quotas = [total * weight / weight_sum for weight in weights]
    counts = [math.floor(quota) for quota in quotas]
    remainder = total - sum(counts)
    order = sorted(
        range(len(weights)),
        key=lambda index: (-(quotas[index] - counts[index]), index),
    )
    for index in order[:remainder]:
        counts[index] += 1
    return counts


def _broad_period(hour: int) -> str:
    if hour < 10:
        return "早朝・朝（3–9時）"
    if hour < 16:
        return "昼（10–15時）"
    if hour < 21:
        return "夕方（16–20時）"
    return "夜間（21時以降）"


def build_time_distributions(
    input_path: Path,
    destination_zone: str = DEFAULT_DESTINATION_ZONE,
    *,
    target_visitors: int = DEFAULT_MEITETSU_VISITORS,
    facility_uses: Iterable[FacilityUse] = FACILITY_USES,
) -> dict[str, Any]:
    """施設分類と目的を同時に限定し、用途別の到着時間分布を集計する。"""

    if target_visitors < 0:
        raise ValueError("target_visitors must be non-negative")
    rows, header, header_line = read_time_table(input_path)
    zone_column = header[0]
    if zone_column in {FACILITY_COLUMN, PURPOSE_COLUMN, TOTAL_COLUMN}:
        raise ValueError("先頭列を中ゾーンコードとして解釈できません")
    time_columns = [
        (name, hour)
        for name in header
        if (hour := parse_time_column(name)) is not None
    ]
    if not time_columns:
        raise ValueError("時間列がありません")

    specs = tuple(facility_uses)
    counts = {spec.key: defaultdict(int) for spec in specs}
    unknown_counts: dict[str, int] = defaultdict(int)
    selected_row_totals: dict[str, int] = defaultdict(int)
    mismatch_rows = 0
    normalized_destination = normalize_zone(destination_zone)

    for row in rows:
        if normalize_zone(row[zone_column]) != normalized_destination:
            continue
        for spec in specs:
            if (
                row[FACILITY_COLUMN] != spec.destination_facility
                or row[PURPOSE_COLUMN] not in spec.purposes
            ):
                continue
            line_no = int(row["__line__"])
            hourly_sum = 0
            for column, hour in time_columns:
                value = parse_count(row[column], column=column, line_no=line_no)
                counts[spec.key][hour] += value
                hourly_sum += value
            unknown = parse_count(
                row.get(UNKNOWN_COLUMN, "0"),
                column=UNKNOWN_COLUMN,
                line_no=line_no,
            )
            total = parse_count(
                row[TOTAL_COLUMN], column=TOTAL_COLUMN, line_no=line_no
            )
            unknown_counts[spec.key] += unknown
            selected_row_totals[spec.key] += total
            if hourly_sum + unknown != total:
                mismatch_rows += 1

    facilities: dict[str, Any] = {}
    ordered_hours = [hour for _, hour in time_columns]
    commercial_target_counts: list[int] = []
    for spec in specs:
        total_known = sum(counts[spec.key].values())
        projected = (
            largest_remainder(
                target_visitors, [counts[spec.key][hour] for hour in ordered_hours]
            )
            if spec.key == "commercial"
            else [0] * len(ordered_hours)
        )
        if spec.key == "commercial":
            commercial_target_counts = projected
        hourly = []
        broad_counts: dict[str, int] = defaultdict(int)
        broad_target_counts: dict[str, int] = defaultdict(int)
        last_hour = max(ordered_hours)
        for hour, projected_count in zip(ordered_hours, projected, strict=True):
            count = counts[spec.key][hour]
            period = _broad_period(hour)
            broad_counts[period] += count
            broad_target_counts[period] += projected_count
            hourly.append(
                {
                    "hour": hour,
                    "label": f"{hour}時以降" if hour == last_hour else f"{hour}時台",
                    "trip_count": count,
                    "share": count / total_known if total_known else 0.0,
                    "share_percent": (
                        count / total_known * 100 if total_known else 0.0
                    ),
                    "projected_target_visitors": (
                        projected_count if spec.key == "commercial" else None
                    ),
                }
            )
        peak = max(hourly, key=lambda item: item["trip_count"], default=None)
        facilities[spec.key] = {
            "name": spec.name,
            "destination_facility": spec.destination_facility,
            "included_purposes": list(spec.purposes),
            "known_time_trips": total_known,
            "unknown_time_trips": unknown_counts[spec.key],
            "selected_row_total_trips": selected_row_totals[spec.key],
            "known_time_share_percent": (
                total_known / selected_row_totals[spec.key] * 100
                if selected_row_totals[spec.key]
                else 0.0
            ),
            "peak_hour": peak,
            "hourly_distribution": hourly,
            "broad_period_distribution": [
                {
                    "period": period,
                    "trip_count": broad_counts[period],
                    "share_percent": (
                        broad_counts[period] / total_known * 100
                        if total_known
                        else 0.0
                    ),
                    "projected_target_visitors": (
                        broad_target_counts[period]
                        if spec.key == "commercial"
                        else None
                    ),
                }
                for period in (
                    "早朝・朝（3–9時）",
                    "昼（10–15時）",
                    "夕方（16–20時）",
                    "夜間（21時以降）",
                )
            ],
        }

    if not facilities.get("commercial", {}).get("known_time_trips"):
        raise ValueError(
            f"中ゾーン{normalized_destination}の商業用途到着トリップが0件です"
        )
    if sum(commercial_target_counts) != target_visitors:
        raise AssertionError("名鉄跡地想定人数の時間帯按分に失敗しました")

    return {
        "schema_version": 1,
        "generated_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(
            timespec="seconds"
        ),
        "method": "destination_facility_and_purpose_by_arrival_hour",
        "scope": {
            "destination_middle_zone": normalized_destination,
            "zone_column_as_exported": zone_column,
            "zone_column_interpretation": (
                "集計条件の粒度が中ゾーンであり、各行の先頭値を"
                "到着中ゾーンコードとして解釈"
            ),
            "time_dimension": "移動終了時（到着時間帯）",
            "target_site": "名鉄百貨店跡地",
            "target_visitors": target_visitors,
        },
        "source": {
            "path": str(input_path.resolve()),
            "sha256": sha256_file(input_path),
            "header_line": header_line,
        },
        "facilities": facilities,
        "audit": {
            "selected_row_time_total_mismatch_rows": mismatch_rows,
            "commercial_projected_total": sum(commercial_target_counts),
        },
        "limitations": [
            (
                "PTの拡大トリップ数による典型1日の到着分布であり、"
                "名鉄跡地固有の実測来館時刻ではない。"
            ),
            (
                "時間帯構成は大型規模小売店かつ指定目的の中ゾーン5到着者を"
                "代理としている。"
            ),
            (
                "到着分布だけでは同時滞在人数を求められず、別途、"
                "滞在時間または退出時刻が必要である。"
            ),
            "平日・休日の区別は、この集計表だけからは付けられない。",
        ],
    }


def write_outputs(
    result: dict[str, Any], output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "time_distribution_by_facility_use.json"
    csv_path = output_dir / "time_distribution_by_facility_use.csv"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = (
        "facility_key",
        "facility_name",
        "destination_facility",
        "hour",
        "label",
        "trip_count",
        "share_percent",
        "projected_target_visitors",
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for key, facility in result["facilities"].items():
            for row in facility["hourly_distribution"]:
                writer.writerow(
                    {
                        "facility_key": key,
                        "facility_name": facility["name"],
                        "destination_facility": facility["destination_facility"],
                        "hour": row["hour"],
                        "label": row["label"],
                        "trip_count": row["trip_count"],
                        "share_percent": f'{row["share_percent"]:.6f}',
                        "projected_target_visitors": (
                            ""
                            if row["projected_target_visitors"] is None
                            else row["projected_target_visitors"]
                        ),
                    }
                )
    return json_path, csv_path


def _prepare_matplotlib() -> Any:
    import os
    import tempfile

    os.environ.setdefault(
        "MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "ppflow-matplotlib")
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in (
        "Hiragino Sans",
        "Yu Gothic",
        "Noto Sans CJK JP",
        "IPAexGothic",
    ):
        if candidate in available:
            plt.rcParams["font.family"] = candidate
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["svg.fonttype"] = "none"
    return plt


def write_charts(result: dict[str, Any], output_dir: Path) -> list[Path]:
    """教授説明用の比較図と、名鉄跡地の商業到着人数図を出力する。"""

    plt = _prepare_matplotlib()
    output_dir.mkdir(parents=True, exist_ok=True)
    colors = {
        "commercial": "#2F6BFF",
        "office": "#08A37A",
        "hotel": "#F28E2B",
    }
    facilities = result["facilities"]
    commercial = facilities["commercial"]
    hours = [row["hour"] for row in commercial["hourly_distribution"]]

    paths: list[Path] = []
    figure, axis = plt.subplots(figsize=(13, 7.2))
    for key, facility in facilities.items():
        shares = [
            row["share_percent"] for row in facility["hourly_distribution"]
        ]
        axis.plot(
            hours,
            shares,
            marker="o",
            markersize=4,
            linewidth=2.4,
            color=colors[key],
            label=(
                f'{facility["name"]}'
                f'（{facility["known_time_trips"]:,}トリップ）'
            ),
        )
    axis.set_title(
        "施設用途によって異なる到着時間帯",
        fontsize=18,
        fontweight="bold",
        loc="left",
    )
    axis.set_xlabel("移動終了時（到着時間帯）")
    axis.set_ylabel("各用途の時刻判明トリップに占める割合（%）")
    axis.set_xticks(
        hours, [f"{hour}" if hour < 27 else "27+" for hour in hours]
    )
    axis.grid(color="#DCE2EA", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.legend(frameon=False, ncol=3, loc="upper right")
    axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle(
        "中京PT 到着時間分布｜"
        f'到着中ゾーン {result["scope"]["destination_middle_zone"]}',
        fontsize=13,
        color="#4D5868",
        x=0.125,
        ha="left",
        y=0.98,
    )
    figure.text(
        0.01,
        0.012,
        (
            "施設分類と目的を同時に限定。PTの「移動終了時」を到着時刻として集計。"
            "27+は27時以降。値は拡大トリップ数。"
        ),
        fontsize=9,
        color="#5B6472",
    )
    figure.tight_layout(rect=(0, 0.045, 1, 0.94))
    for suffix in (".png", ".svg"):
        path = (
            output_dir / "arrival_time_distribution_all_facilities"
        ).with_suffix(suffix)
        figure.savefig(
            path, dpi=200, bbox_inches="tight", facecolor="white"
        )
        paths.append(path)
    plt.close(figure)

    target = result["scope"]["target_visitors"]
    projected = [
        row["projected_target_visitors"]
        for row in commercial["hourly_distribution"]
    ]
    peak = commercial["peak_hour"]
    figure, axis = plt.subplots(figsize=(13, 7.5))
    bars = axis.bar(hours, projected, color="#2F6BFF", width=0.78)
    peak_index = hours.index(peak["hour"])
    bars[peak_index].set_color("#F05A47")
    axis.set_title(
        (
            f"名鉄百貨店跡地｜商業来訪者 {target:,}人の"
            "時間帯別到着想定"
        ),
        fontsize=18,
        fontweight="bold",
        loc="left",
    )
    axis.set_xlabel("到着時間帯")
    axis.set_ylabel("想定到着人数（人／日）")
    axis.set_xticks(
        hours, [f"{hour}" if hour < 27 else "27+" for hour in hours]
    )
    axis.grid(axis="y", color="#DCE2EA", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.tick_params(axis="y", length=0)
    for bar, count in zip(bars, projected, strict=True):
        if count >= max(projected) * 0.2:
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                count,
                f"{count:,}",
                ha="center",
                va="bottom",
                fontsize=9,
            )
    axis.text(
        0.99,
        0.96,
        f'ピーク：{peak["hour"]}時台\n構成比 {peak["share_percent"]:.1f}%',
        transform=axis.transAxes,
        ha="right",
        va="top",
        fontsize=12,
        color="#B33B2E",
        bbox={
            "boxstyle": "round,pad=0.5",
            "facecolor": "#FFF1EF",
            "edgecolor": "none",
        },
    )
    figure.text(
        0.01,
        0.012,
        (
            "中ゾーン5の大型規模小売店・商業目的のPT到着構成比を、"
            f"想定来訪者{target:,}人へ最大剰余法で按分。"
            "実測予測ではなく初期入力分布。"
        ),
        fontsize=9,
        color="#5B6472",
    )
    figure.tight_layout(rect=(0, 0.045, 1, 0.97))
    for suffix in (".png", ".svg"):
        path = (
            output_dir / "meitetsu_commercial_arrivals_by_hour"
        ).with_suffix(suffix)
        figure.savefig(
            path, dpi=200, bbox_inches="tight", facecolor="white"
        )
        paths.append(path)
    plt.close(figure)
    return paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="中京PTから施設用途別の到着時間分布を作成します。"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--destination-zone", default=DEFAULT_DESTINATION_ZONE
    )
    parser.add_argument(
        "--target-visitors", type=int, default=DEFAULT_MEITETSU_VISITORS
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-charts", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        raise FileNotFoundError(f"Input CSV not found: {args.input}")
    result = build_time_distributions(
        args.input,
        args.destination_zone,
        target_visitors=args.target_visitors,
    )
    charts = (
        []
        if args.skip_charts
        else write_charts(result, args.output_dir)
    )
    result["outputs"] = {
        "charts": [str(path.resolve()) for path in charts]
    }
    json_path, csv_path = write_outputs(result, args.output_dir)
    print(f'到着中ゾーン: {result["scope"]["destination_middle_zone"]}')
    for facility in result["facilities"].values():
        peak = facility["peak_hour"]
        print(
            f'{facility["name"]}: {facility["known_time_trips"]:,} trips, '
            f'peak {peak["hour"]}:00 ({peak["share_percent"]:.1f}%)'
        )
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    for path in charts:
        print(f"chart: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
