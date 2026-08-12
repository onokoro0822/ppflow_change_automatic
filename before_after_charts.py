#!/usr/bin/env python3
"""名鉄百貨店閉店シナリオのBefore/After集計グラフを作成する。"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


os.environ.setdefault(
    "MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "ppflow-matplotlib")
)

import matplotlib.pyplot as plt
from matplotlib import font_manager


DEFAULT_SCENARIO_DIR = Path(
    "output/facility_closure/meitetsu_nagoya_closure_demo"
)
AREA_RADII_M = (50, 200, 500, 1000)
AREA_LABELS = ("50m", "200m", "500m", "1km")
SOURCE_RING_LABELS = (
    "0–50m",
    "50–200m",
    "200–500m",
    "500m–1km",
    "1–2km",
    "2–5km",
    "5km以上",
)
TRIP_DISTANCE_LABELS = (
    "0–0.5km",
    "0.5–2km",
    "2–5km",
    "5–10km",
    "10–18km",
    "18km以上",
)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """2点の緯度経度間の大円距離をメートルで返す。"""

    earth_radius_m = 6_371_008.8
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_m * math.asin(math.sqrt(a))


def _source_ring(distance_m: float) -> str:
    """旧本店代表点からの距離を可視化用の帯へ分類する。"""

    if distance_m <= 50:
        return "0–50m"
    if distance_m <= 200:
        return "50–200m"
    if distance_m <= 500:
        return "200–500m"
    if distance_m <= 1000:
        return "500m–1km"
    if distance_m <= 2000:
        return "1–2km"
    if distance_m <= 5000:
        return "2–5km"
    return "5km以上"


def _trip_distance_band(distance_km: float) -> str:
    """出発地から買い物先までの距離を詳細な距離帯へ分類する。"""

    if distance_km < 0.5:
        return "0–0.5km"
    if distance_km < 2:
        return "0.5–2km"
    if distance_km < 5:
        return "2–5km"
    if distance_km < 10:
        return "5–10km"
    if distance_km < 18:
        return "10–18km"
    return "18km以上"


def _load_inputs(scenario_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """再配分サマリーと変更対象買い物トリップを読み込む。"""

    summary = json.loads((scenario_dir / "summary.json").read_text(encoding="utf-8"))
    visits: list[dict[str, Any]] = []
    with (scenario_dir / "selected_visits.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        for row in csv.DictReader(handle):
            visits.append(
                {
                    **row,
                    "original_destination_lon": float(row["original_destination_lon"]),
                    "original_destination_lat": float(row["original_destination_lat"]),
                    "new_destination_lon": float(row["new_destination_lon"]),
                    "new_destination_lat": float(row["new_destination_lat"]),
                    "incoming_distance_before_km": float(
                        row["incoming_distance_before_km"]
                    ),
                    "incoming_distance_after_km": float(
                        row["incoming_distance_after_km"]
                    ),
                }
            )
    return summary, visits


def aggregate_before_after(
    summary: dict[str, Any], visits: list[dict[str, Any]]
) -> dict[str, Any]:
    """名古屋駅周辺残留人数と距離分布を集計する。"""

    source = summary["scenario"]["source_facility"]
    source_lon = float(source["longitude"])
    source_lat = float(source["latitude"])
    people = {str(row["person_id"]) for row in visits}
    people_inside: dict[int, set[str]] = defaultdict(set)
    visits_inside = Counter()
    source_rings = Counter()
    incoming_before = Counter()
    incoming_after = Counter()

    for row in visits:
        after_source_distance_m = haversine_m(
            source_lon,
            source_lat,
            row["new_destination_lon"],
            row["new_destination_lat"],
        )
        row["after_source_distance_m"] = after_source_distance_m
        source_rings[_source_ring(after_source_distance_m)] += 1
        incoming_before[
            _trip_distance_band(row["incoming_distance_before_km"])
        ] += 1
        incoming_after[
            _trip_distance_band(row["incoming_distance_after_km"])
        ] += 1
        for radius_m in AREA_RADII_M:
            if after_source_distance_m <= radius_m:
                people_inside[radius_m].add(str(row["person_id"]))
                visits_inside[radius_m] += 1

    total_people = len(people)
    total_visits = len(visits)
    area_rows = []
    for radius_m, label in zip(AREA_RADII_M, AREA_LABELS):
        inside_people = len(people_inside[radius_m])
        inside_visits = visits_inside[radius_m]
        area_rows.append(
            {
                "radius_m": radius_m,
                "radius_label": label,
                "before_people_inside": total_people,
                "after_people_inside": inside_people,
                "after_people_outside": total_people - inside_people,
                "after_people_inside_pct": inside_people / total_people * 100,
                "after_people_outside_pct": (total_people - inside_people)
                / total_people
                * 100,
                "before_visits_inside": total_visits,
                "after_visits_inside": inside_visits,
                "after_visits_outside": total_visits - inside_visits,
            }
        )

    return {
        "total_people": total_people,
        "total_visits": total_visits,
        "area_rows": area_rows,
        "source_rings": source_rings,
        "incoming_before": incoming_before,
        "incoming_after": incoming_after,
        "incoming_before_mean_km": float(
            summary["distance_km"]["incoming_before_mean"]
        ),
        "incoming_after_mean_km": float(
            summary["distance_km"]["incoming_after_mean"]
        ),
        "source_label": source["label"],
    }


def _configure_japanese_font() -> None:
    """macOS上の日本語フォントをMatplotlibへ設定する。"""

    font_candidates = (
        Path("/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc"),
        Path("/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    )
    for font_path in font_candidates:
        if font_path.exists():
            font_manager.fontManager.addfont(str(font_path))
            plt.rcParams["font.family"] = font_manager.FontProperties(
                fname=str(font_path)
            ).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False


def _style_axis(axis: Any) -> None:
    """出力グラフで共通する余白・罫線・枠線を整える。"""

    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="x", color="#d9d9d9", linewidth=0.8, alpha=0.7)
    axis.set_axisbelow(True)


def plot_station_area_people(metrics: dict[str, Any], output_path: Path) -> None:
    """Afterで旧本店代表点周辺に残る人と外れる人を積み上げ棒で描く。"""

    rows = metrics["area_rows"]
    labels = [row["radius_label"] for row in rows]
    inside = [row["after_people_inside"] for row in rows]
    outside = [row["after_people_outside"] for row in rows]
    total = metrics["total_people"]

    figure, axis = plt.subplots(figsize=(10.8, 5.8))
    y_positions = list(range(len(labels)))
    axis.barh(y_positions, inside, color="#2563A5", label="範囲内に再配分")
    axis.barh(
        y_positions,
        outside,
        left=inside,
        color="#ED7D31",
        label="範囲外へ再配分",
    )
    for y, inside_count, outside_count in zip(y_positions, inside, outside):
        if inside_count:
            axis.text(
                inside_count / 2,
                y,
                f"{inside_count:,}人\n{inside_count / total:.1%}",
                ha="center",
                va="center",
                color="white",
                fontsize=10,
            )
        axis.text(
            inside_count + outside_count / 2,
            y,
            f"{outside_count:,}人\n{outside_count / total:.1%}",
            ha="center",
            va="center",
            color="white",
            fontsize=10,
        )
    axis.set_yticks(y_positions, labels)
    axis.invert_yaxis()
    axis.set_xlim(0, total)
    axis.set_xlabel("変更対象者数（人）")
    axis.set_title(
        "After：旧名鉄百貨店代表点の周辺に残る人／外れる人",
        loc="left",
        fontsize=16,
        fontweight="normal",
        y=1.13,
    )
    axis.text(
        0,
        1.045,
        f"Beforeでは変更対象{total:,}人全員が代表点50m以内。"
        "範囲内は、Afterの買い物先が少なくとも1件入る人。",
        transform=axis.transAxes,
        fontsize=10,
        color="#555555",
    )
    axis.legend(frameon=False, ncol=2, loc="lower center", bbox_to_anchor=(0.5, -0.25))
    _style_axis(axis)
    figure.tight_layout()
    figure.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def plot_after_destination_rings(metrics: dict[str, Any], output_path: Path) -> None:
    """Afterの買い物先を旧本店代表点からの距離帯別に描く。"""

    counts = [metrics["source_rings"][label] for label in SOURCE_RING_LABELS]
    total = metrics["total_visits"]
    figure, axis = plt.subplots(figsize=(10.8, 5.8))
    bars = axis.barh(SOURCE_RING_LABELS, counts, color="#ED7D31")
    axis.invert_yaxis()
    for bar, count in zip(bars, counts):
        axis.text(
            bar.get_width() + total * 0.012,
            bar.get_y() + bar.get_height() / 2,
            f"{count:,}件（{count / total:.1%}）",
            va="center",
            fontsize=10,
        )
    axis.set_xlim(0, max(counts) * 1.25)
    axis.set_xlabel("再配分された買い物トリップ（件）")
    axis.set_title(
        "After：旧名鉄百貨店代表点から見た買い物先の分布",
        loc="left",
        fontsize=16,
        fontweight="normal",
        y=1.13,
    )
    axis.text(
        0,
        1.045,
        "代表点500m超へ1,526件（76.8%）が移動。施設名・営業状態は未確定。",
        transform=axis.transAxes,
        fontsize=10,
        color="#555555",
    )
    _style_axis(axis)
    figure.tight_layout()
    figure.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def plot_incoming_distance(metrics: dict[str, Any], output_path: Path) -> None:
    """出発地から買い物先までの距離分布をBefore/Afterで比較する。"""

    before = [metrics["incoming_before"][label] for label in TRIP_DISTANCE_LABELS]
    after = [metrics["incoming_after"][label] for label in TRIP_DISTANCE_LABELS]
    x_positions = list(range(len(TRIP_DISTANCE_LABELS)))
    width = 0.38
    figure, axis = plt.subplots(figsize=(10.8, 5.8))
    before_bars = axis.bar(
        [x - width / 2 for x in x_positions],
        before,
        width,
        color="#2563A5",
        label="Before",
    )
    after_bars = axis.bar(
        [x + width / 2 for x in x_positions],
        after,
        width,
        color="#ED7D31",
        label="After",
    )
    for bars in (before_bars, after_bars):
        for bar in bars:
            if bar.get_height():
                axis.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 12,
                    f"{int(bar.get_height()):,}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )
    axis.set_xticks(x_positions, TRIP_DISTANCE_LABELS)
    axis.set_ylabel("買い物トリップ（件）")
    axis.set_title(
        "出発地から買い物先までの距離：Before / After",
        loc="left",
        fontsize=16,
        fontweight="normal",
        y=1.13,
    )
    axis.text(
        0,
        1.045,
        f"平均距離は{metrics['incoming_before_mean_km']:.2f}kmから"
        f"{metrics['incoming_after_mean_km']:.2f}kmへ増加。",
        transform=axis.transAxes,
        fontsize=10,
        color="#555555",
    )
    axis.legend(frameon=False, ncol=2)
    axis.grid(axis="y", color="#d9d9d9", linewidth=0.8, alpha=0.7)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    figure.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def plot_overview(metrics: dict[str, Any], output_path: Path) -> None:
    """主要3指標を進捗報告向けの16:9一枚図にまとめる。"""

    total_people = metrics["total_people"]
    total_visits = metrics["total_visits"]
    area_500 = next(row for row in metrics["area_rows"] if row["radius_m"] == 500)
    ring_counts = [metrics["source_rings"][label] for label in SOURCE_RING_LABELS]
    before = [metrics["incoming_before"][label] for label in TRIP_DISTANCE_LABELS]
    after = [metrics["incoming_after"][label] for label in TRIP_DISTANCE_LABELS]

    figure = plt.figure(figsize=(16, 9))
    grid = figure.add_gridspec(2, 2, height_ratios=(1, 1.15), hspace=0.42, wspace=0.27)
    axis_area = figure.add_subplot(grid[0, 0])
    axis_rings = figure.add_subplot(grid[0, 1])
    axis_distance = figure.add_subplot(grid[1, :])

    inside_500 = area_500["after_people_inside"]
    outside_500 = area_500["after_people_outside"]
    axis_area.barh([0], [inside_500], color="#2563A5", label="500m以内")
    axis_area.barh(
        [0], [outside_500], left=[inside_500], color="#ED7D31", label="500m超"
    )
    axis_area.text(
        inside_500 / 2,
        0,
        f"500m以内\n{inside_500:,}人\n{inside_500 / total_people:.1%}",
        ha="center",
        va="center",
        color="white",
        fontsize=12,
    )
    axis_area.text(
        inside_500 + outside_500 / 2,
        0,
        f"500m超\n{outside_500:,}人\n{outside_500 / total_people:.1%}",
        ha="center",
        va="center",
        color="white",
        fontsize=12,
    )
    axis_area.set_xlim(0, total_people)
    axis_area.set_yticks([])
    axis_area.set_xlabel("変更対象者（人）")
    axis_area.set_title("① Afterの買い物先：代表点500m内外", loc="left", fontsize=15)
    _style_axis(axis_area)

    bars = axis_rings.barh(SOURCE_RING_LABELS, ring_counts, color="#ED7D31")
    axis_rings.invert_yaxis()
    for bar, count in zip(bars, ring_counts):
        axis_rings.text(
            count + 10,
            bar.get_y() + bar.get_height() / 2,
            f"{count:,}",
            va="center",
            fontsize=9,
        )
    axis_rings.set_xlim(0, max(ring_counts) * 1.22)
    axis_rings.set_xlabel("買い物トリップ（件）")
    axis_rings.set_title("② Afterの再配分先距離帯", loc="left", fontsize=15)
    _style_axis(axis_rings)

    x_positions = list(range(len(TRIP_DISTANCE_LABELS)))
    width = 0.38
    before_bars = axis_distance.bar(
        [x - width / 2 for x in x_positions],
        before,
        width,
        color="#2563A5",
        label="Before",
    )
    after_bars = axis_distance.bar(
        [x + width / 2 for x in x_positions],
        after,
        width,
        color="#ED7D31",
        label="After",
    )
    for bars in (before_bars, after_bars):
        for bar in bars:
            if bar.get_height():
                axis_distance.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 11,
                    f"{int(bar.get_height()):,}",
                    ha="center",
                    fontsize=9,
                )
    axis_distance.set_xticks(x_positions, TRIP_DISTANCE_LABELS)
    axis_distance.set_ylabel("買い物トリップ（件）")
    axis_distance.set_title(
        "③ 出発地から買い物先までの距離分布",
        loc="left",
        fontsize=15,
    )
    axis_distance.legend(frameon=False, ncol=2)
    axis_distance.grid(axis="y", color="#d9d9d9", linewidth=0.8, alpha=0.7)
    axis_distance.set_axisbelow(True)
    axis_distance.spines[["top", "right"]].set_visible(False)

    figure.suptitle(
        "名鉄百貨店本店・買い物トリップ再配分 Before / After",
        x=0.06,
        y=0.98,
        ha="left",
        fontsize=22,
        fontweight="normal",
    )
    figure.text(
        0.06,
        0.93,
        f"暫定50m条件の{total_people:,}人・{total_visits:,}件を全数再配分。"
        f"代表点500m超へ{outside_500:,}人、到着距離平均は"
        f"{metrics['incoming_before_mean_km']:.2f}km → "
        f"{metrics['incoming_after_mean_km']:.2f}km。",
        fontsize=12,
        color="#555555",
    )
    figure.text(
        0.06,
        0.025,
        "注：500m超は『名古屋駅を利用しなくなった』ことではなく、Afterの買い物先が"
        "旧本店代表点から500m超になったことを示す。施設境界・営業状態は未確定。",
        fontsize=10,
        color="#555555",
    )
    figure.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _write_csv_outputs(metrics: dict[str, Any], output_dir: Path) -> None:
    """グラフの根拠となる集計表をCSVへ保存する。"""

    with (output_dir / "station_area_retention.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        fieldnames = list(metrics["area_rows"][0].keys())
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metrics["area_rows"])

    with (output_dir / "after_destination_distance_bands.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["distance_band", "after_visits", "after_visit_pct"]
        )
        writer.writeheader()
        for label in SOURCE_RING_LABELS:
            count = metrics["source_rings"][label]
            writer.writerow(
                {
                    "distance_band": label,
                    "after_visits": count,
                    "after_visit_pct": count / metrics["total_visits"] * 100,
                }
            )

    with (output_dir / "incoming_distance_distribution.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "distance_band",
                "before_visits",
                "after_visits",
                "difference",
            ],
        )
        writer.writeheader()
        for label in TRIP_DISTANCE_LABELS:
            before = metrics["incoming_before"][label]
            after = metrics["incoming_after"][label]
            writer.writerow(
                {
                    "distance_band": label,
                    "before_visits": before,
                    "after_visits": after,
                    "difference": after - before,
                }
            )


def create_outputs(scenario_dir: Path, output_dir: Path) -> dict[str, Any]:
    """集計表と4種類のPNGグラフをまとめて生成する。"""

    summary, visits = _load_inputs(scenario_dir)
    metrics = aggregate_before_after(summary, visits)
    output_dir.mkdir(parents=True, exist_ok=True)
    _configure_japanese_font()
    _write_csv_outputs(metrics, output_dir)
    plot_station_area_people(metrics, output_dir / "station_area_people.png")
    plot_after_destination_rings(metrics, output_dir / "after_destination_rings.png")
    plot_incoming_distance(metrics, output_dir / "incoming_distance_before_after.png")
    plot_overview(metrics, output_dir / "before_after_overview.png")
    return metrics


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を解釈する。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-dir", type=Path, default=DEFAULT_SCENARIO_DIR)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    """既定シナリオのBefore/Afterグラフを生成して要点を表示する。"""

    args = parse_args()
    output_dir = args.output_dir or args.scenario_dir / "charts"
    metrics = create_outputs(args.scenario_dir, output_dir)
    area_500 = next(row for row in metrics["area_rows"] if row["radius_m"] == 500)
    print(f"Output: {output_dir}")
    print(
        "500m超へ再配分された人: "
        f"{area_500['after_people_outside']:,} / {metrics['total_people']:,} "
        f"({area_500['after_people_outside_pct']:.1f}%)"
    )
    print(
        "到着距離平均: "
        f"{metrics['incoming_before_mean_km']:.2f}km -> "
        f"{metrics['incoming_after_mean_km']:.2f}km"
    )


if __name__ == "__main__":
    main()
