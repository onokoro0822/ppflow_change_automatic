"""Build facility-use origin distributions from Chukyo PT OD tables.

The currently available aggregate OD table contains origin zone, destination
zone, detailed purpose, mode and expanded trip counts, but no destination
facility column. In that case the three facility-use profiles are purpose-based
proxies. If a future table also contains ``到着施設``, the same program uses the
facility and purpose conditions together and reports an exact-facility method.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parent
PT_DATA_DIR = PROJECT_DIR.parent / "中京PTデータ"
DEFAULT_INPUT = PT_DATA_DIR / "chukyo_pt_2022_od_purpose_mode_raw.csv"
DEFAULT_ZONE_CODE_XLSX = PT_DATA_DIR / "中ゾーンコード表" / "pt_system_code_table.xlsx"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "chukyo_pt_origin_distribution"

ORIGIN_COLUMN = "出発地（中ゾーン）"
DESTINATION_COLUMN = "到着地（中ゾーン）"
PURPOSE_COLUMN = "目的（細分類）"
FACILITY_COLUMN = "到着施設"
TOTAL_COLUMN = "合計"
REQUIRED_COLUMNS = {ORIGIN_COLUMN, DESTINATION_COLUMN, PURPOSE_COLUMN, TOTAL_COLUMN}


@dataclass(frozen=True)
class FacilityUse:
    key: str
    name: str
    destination_facility: str
    purposes: tuple[str, ...]


FACILITY_USES = (
    FacilityUse(
        key="commercial",
        name="商業施設",
        destination_facility="大型規模小売店",
        purposes=(
            "日常的な家事・買物",
            "日常的でない買物",
            "食事・社交・喫茶",
            "娯楽・文化活動",
            "観光・行楽・レジャー",
            "その他の自由目的",
        ),
    ),
    FacilityUse(
        key="office",
        name="オフィス",
        destination_facility="事務所・会社・銀行・郵便局",
        purposes=(
            "出勤（勤務先へ）",
            "打合せ・会議・仕事",
            "書類持参・受領・集金",
            "販売・配達・仕入れ・購入",
            "作業・修理",
            "その他の業務目的",
            "帰社・帰校(会社や学校へ帰る)",
        ),
    ),
    FacilityUse(
        key="hotel",
        name="ホテル",
        destination_facility="宿泊施設・ホテル",
        purposes=(
            "観光・行楽・レジャー",
            "打合せ・会議・仕事",
            "食事・社交・喫茶",
            "その他の自由目的",
            "その他の業務目的",
        ),
    ),
)


@dataclass(frozen=True)
class ParsedTable:
    rows: Iterable[dict[str, str]]
    header: tuple[str, ...]
    header_line: int


@dataclass(frozen=True)
class MiddleZoneInfo:
    prefectures: tuple[str, ...]
    municipalities: tuple[str, ...]
    label: str


def normalize_zone(value: object) -> str:
    """Normalize zone codes so values such as 5 and 5.0 compare equally."""

    text = str(value).strip()
    if re.fullmatch(r"[+-]?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def parse_count(value: str, *, column: str, line_no: int) -> int:
    text = value.strip().replace(",", "")
    if not text or text in {"-", "―"}:
        return 0
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"line {line_no}: invalid count in {column}: {value!r}") from exc
    if not number.is_integer():
        raise ValueError(f"line {line_no}: non-integer expanded count in {column}: {value!r}")
    return int(number)


def read_pt_table(path: Path) -> ParsedTable:
    """Locate the real header below the metadata block and return row dicts."""

    source = path.open("r", encoding="utf-8-sig", newline="")
    reader = csv.reader(source)
    for line_no, row in enumerate(reader, start=1):
        normalized = tuple(cell.strip() for cell in row)
        if REQUIRED_COLUMNS.issubset(normalized):
            header = normalized
            break
    else:
        source.close()
        raise ValueError(
            f"Required PT columns were not found in {path}: {sorted(REQUIRED_COLUMNS)}"
        )

    def iter_rows() -> Iterator[dict[str, str]]:
        try:
            for row in reader:
                current_line = reader.line_num
                if not row or not any(cell.strip() for cell in row):
                    continue
                if len(row) < len(header):
                    row = [*row, *([""] * (len(header) - len(row)))]
                yield {name: row[index].strip() for index, name in enumerate(header)} | {
                    "__line__": str(current_line)
                }
        finally:
            source.close()

    return ParsedTable(rows=iter_rows(), header=header, header_line=line_no)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _xlsx_shared_strings(archive: ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(archive.read(name))
    return [
        "".join(node.text or "" for node in item.iter(f"{namespace}t"))
        for item in root.findall(f"{namespace}si")
    ]


def _xlsx_sheet_path(archive: ZipFile, sheet_name: str) -> str:
    main_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    package_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall(f"{package_ns}Relationship")
    }
    sheets = workbook.find(f"{main_ns}sheets")
    if sheets is None:
        raise ValueError("XLSX workbook has no sheets")
    for sheet in sheets:
        if sheet.attrib.get("name") == sheet_name:
            target = targets[sheet.attrib[f"{rel_ns}id"]]
            return target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
    raise ValueError(f"Sheet {sheet_name!r} was not found in XLSX")


def _xlsx_cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> str:
    main_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{main_ns}t"))
    value = cell.find(f"{main_ns}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared_strings[int(value.text)]
    return value.text


def load_middle_zone_labels(path: Path, sheet_name: str = "Sheet5") -> dict[str, MiddleZoneInfo]:
    """Read middle-zone prefecture/municipality labels from the official XLSX.

    This uses only the Python standard library. Sheet5 columns B/C/E contain
    prefecture, municipality and middle-zone code in the supplied code table.
    """

    main_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    collected: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"prefectures": set(), "municipalities": set()}
    )
    with ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_path = _xlsx_sheet_path(archive, sheet_name)
        with archive.open(sheet_path) as sheet:
            for _, element in ET.iterparse(sheet, events=("end",)):
                if element.tag != f"{main_ns}row":
                    continue
                values: dict[str, str] = {}
                for cell in element.findall(f"{main_ns}c"):
                    reference = cell.attrib.get("r", "")
                    column = re.match(r"[A-Z]+", reference)
                    if column and column.group() in {"B", "C", "E"}:
                        values[column.group()] = _xlsx_cell_value(cell, shared_strings).strip()
                zone = normalize_zone(values.get("E", ""))
                prefecture = values.get("B", "")
                municipality = values.get("C", "")
                if zone and prefecture and municipality and zone != "中ゾーンチュウゾ-":
                    collected[zone]["prefectures"].add(prefecture)
                    collected[zone]["municipalities"].add(municipality)
                element.clear()

    result: dict[str, MiddleZoneInfo] = {}
    for zone, names in collected.items():
        prefectures = tuple(sorted(names["prefectures"]))
        municipalities = tuple(sorted(names["municipalities"]))
        municipality_label = "・".join(municipalities[:3])
        if len(municipalities) > 3:
            municipality_label += f"ほか{len(municipalities) - 3}市区町村"
        label = " ".join(filter(None, ("・".join(prefectures), municipality_label)))
        result[zone] = MiddleZoneInfo(prefectures, municipalities, label)
    return result


def _sorted_count_distribution(counts: dict[str, int]) -> list[dict[str, Any]]:
    total = sum(counts.values())
    rows = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {
            "category": category,
            "trip_count": count,
            "share": count / total if total else 0.0,
            "share_percent": 100.0 * count / total if total else 0.0,
        }
        for category, count in rows
    ]


def build_origin_distributions(
    input_csv: Path,
    destination_zones: Sequence[str],
    *,
    zone_labels: dict[str, MiddleZoneInfo] | None = None,
    facility_uses: Sequence[FacilityUse] = FACILITY_USES,
) -> dict[str, Any]:
    """Aggregate independent origin distributions for each facility use."""

    destination_zone_set = {normalize_zone(zone) for zone in destination_zones}
    if not destination_zone_set or "" in destination_zone_set:
        raise ValueError("At least one non-empty destination zone is required")
    table = read_pt_table(input_csv)
    exact_facility = FACILITY_COLUMN in table.header
    method = "destination_facility_and_purpose" if exact_facility else "purpose_proxy"
    labels = zone_labels or {}

    origin_counts: dict[str, dict[str, int]] = {
        spec.key: defaultdict(int) for spec in facility_uses
    }
    origin_mode_counts: dict[str, dict[str, dict[str, int]]] = {
        spec.key: defaultdict(lambda: defaultdict(int)) for spec in facility_uses
    }
    purpose_counts: dict[str, dict[str, int]] = {
        spec.key: defaultdict(int) for spec in facility_uses
    }
    total_mode_counts: dict[str, dict[str, int]] = {
        spec.key: defaultdict(int) for spec in facility_uses
    }
    mode_columns = [
        column
        for column in table.header
        if column
        not in {
            ORIGIN_COLUMN,
            DESTINATION_COLUMN,
            PURPOSE_COLUMN,
            FACILITY_COLUMN,
            TOTAL_COLUMN,
        }
    ]
    source_rows = 0
    matched_destination_rows = 0
    zero_total_rows = 0
    mode_total_mismatch_rows = 0

    for row in table.rows:
        source_rows += 1
        if normalize_zone(row[DESTINATION_COLUMN]) not in destination_zone_set:
            continue
        matched_destination_rows += 1
        line_no = int(row["__line__"])
        total = parse_count(row[TOTAL_COLUMN], column=TOTAL_COLUMN, line_no=line_no)
        if total == 0:
            zero_total_rows += 1
            continue
        origin = normalize_zone(row[ORIGIN_COLUMN])
        purpose = row[PURPOSE_COLUMN]
        mode_values = {
            mode: parse_count(row[mode], column=mode, line_no=line_no)
            for mode in mode_columns
        }
        if sum(mode_values.values()) != total:
            mode_total_mismatch_rows += 1

        for spec in facility_uses:
            if purpose not in spec.purposes:
                continue
            if exact_facility and row[FACILITY_COLUMN] != spec.destination_facility:
                continue
            origin_counts[spec.key][origin] += total
            purpose_counts[spec.key][purpose] += total
            for mode, count in mode_values.items():
                if count:
                    origin_mode_counts[spec.key][origin][mode] += count
                    total_mode_counts[spec.key][mode] += count

    facilities: dict[str, Any] = {}
    for spec in facility_uses:
        counts = origin_counts[spec.key]
        total = sum(counts.values())
        ordered_origins = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        origin_distribution = []
        for rank, (origin, count) in enumerate(ordered_origins, start=1):
            zone = labels.get(origin)
            origin_distribution.append(
                {
                    "rank": rank,
                    "origin_zone": origin,
                    "origin_label": zone.label if zone else "",
                    "origin_prefectures": list(zone.prefectures) if zone else [],
                    "origin_municipalities": list(zone.municipalities) if zone else [],
                    "trip_count": count,
                    "share": count / total if total else 0.0,
                    "share_percent": 100.0 * count / total if total else 0.0,
                    "is_intrazonal": origin in destination_zone_set,
                    "mode_counts": dict(
                        sorted(
                            origin_mode_counts[spec.key][origin].items(),
                            key=lambda item: (-item[1], item[0]),
                        )
                    ),
                }
            )
        facilities[spec.key] = {
            "name": spec.name,
            "destination_facility_reference": spec.destination_facility,
            "purposes": list(spec.purposes),
            "total_trips": total,
            "origin_zone_count": len(ordered_origins),
            "origin_distribution": origin_distribution,
            "purpose_distribution": _sorted_count_distribution(purpose_counts[spec.key]),
            "mode_distribution": _sorted_count_distribution(total_mode_counts[spec.key]),
        }

    overlap = sorted(
        {
            purpose
            for index, spec in enumerate(facility_uses)
            for other in facility_uses[index + 1 :]
            for purpose in set(spec.purposes).intersection(other.purposes)
        }
    )
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
        "source": {
            "path": str(input_csv.resolve()),
            "sha256": sha256_file(input_csv),
            "header_line": table.header_line,
            "source_rows": source_rows,
        },
        "scope": {
            "destination_zones": sorted(destination_zone_set),
            "destination_zone_labels": {
                zone: labels[zone].label for zone in sorted(destination_zone_set) if zone in labels
            },
        },
        "method": method,
        "exact_destination_facility_condition": exact_facility,
        "unit": "expanded_person_trips",
        "facility_profiles_are_independent": True,
        "overlapping_purposes": overlap,
        "limitations": (
            []
            if exact_facility
            else [
                "The source OD table has no 到着施設 column, so facility uses are purpose-based proxies.",
                "Facility profiles overlap in some purposes and must not be summed as mutually exclusive totals.",
                "Origins are immediately preceding activity zones, not necessarily home/residence zones.",
                "The Chukyo PT survey covers a specified weekday in 2022 and excludes residents outside the survey area.",
            ]
        ),
        "audit": {
            "matched_destination_rows": matched_destination_rows,
            "zero_total_rows": zero_total_rows,
            "mode_total_mismatch_rows": mode_total_mismatch_rows,
        },
        "facilities": facilities,
    }


def write_outputs(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "origin_distribution_by_facility_use.json"
    csv_path = output_dir / "origin_distribution_by_facility_use.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fieldnames = (
        "facility_key",
        "facility_name",
        "method",
        "destination_zones",
        "rank",
        "origin_zone",
        "origin_label",
        "origin_prefectures",
        "origin_municipalities",
        "trip_count",
        "share",
        "share_percent",
        "is_intrazonal",
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for key, facility in result["facilities"].items():
            for origin in facility["origin_distribution"]:
                writer.writerow(
                    {
                        "facility_key": key,
                        "facility_name": facility["name"],
                        "method": result["method"],
                        "destination_zones": ";".join(result["scope"]["destination_zones"]),
                        "rank": origin["rank"],
                        "origin_zone": origin["origin_zone"],
                        "origin_label": origin["origin_label"],
                        "origin_prefectures": ";".join(origin["origin_prefectures"]),
                        "origin_municipalities": ";".join(origin["origin_municipalities"]),
                        "trip_count": origin["trip_count"],
                        "share": f'{origin["share"]:.10f}',
                        "share_percent": f'{origin["share_percent"]:.6f}',
                        "is_intrazonal": str(origin["is_intrazonal"]).lower(),
                    }
                )
    return json_path, csv_path



def write_summary_table(result: dict[str, Any], output_dir: Path, top_n: int = 15) -> Path:
    """Write a compact, human-readable table of the leading origin zones."""

    if top_n <= 0:
        raise ValueError("top_n must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"origin_distribution_top{top_n}.csv"
    fieldnames = (
        "facility_key",
        "facility_name",
        "rank",
        "origin_zone",
        "origin_label",
        "trip_count",
        "share_percent",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for key, facility in result["facilities"].items():
            for origin in facility["origin_distribution"][:top_n]:
                writer.writerow(
                    {
                        "facility_key": key,
                        "facility_name": facility["name"],
                        "rank": origin["rank"],
                        "origin_zone": origin["origin_zone"],
                        "origin_label": origin["origin_label"],
                        "trip_count": origin["trip_count"],
                        "share_percent": f'{origin["share_percent"]:.6f}',
                    }
                )
    return path


def _origin_chart_label(origin: dict[str, Any]) -> str:
    municipalities = origin.get("origin_municipalities", [])
    if municipalities:
        name = "・".join(municipalities[:2])
        if len(municipalities) > 2:
            name += f"ほか{len(municipalities) - 2}"
    else:
        name = origin.get("origin_label", "") or "名称不明"
    return f'{origin["origin_zone"]}  {name}'


def _plot_origin_bars(
    axis: Any,
    facility: dict[str, Any],
    *,
    top_n: int,
    color: str,
) -> None:
    origins = facility["origin_distribution"][:top_n]
    origins = list(reversed(origins))
    labels = [_origin_chart_label(origin) for origin in origins]
    shares = [origin["share_percent"] for origin in origins]
    counts = [origin["trip_count"] for origin in origins]
    positions = list(range(len(origins)))
    axis.barh(positions, shares, color=color, alpha=0.9)
    axis.set_yticks(positions, labels)
    axis.set_xlabel("用途別到着トリップに占める割合（%）")
    axis.set_title(
        f'{facility["name"]}　合計 {facility["total_trips"]:,}トリップ',
        loc="left",
        fontsize=14,
        fontweight="bold",
    )
    axis.grid(axis="x", color="#D9DEE7", linewidth=0.8)
    axis.set_axisbelow(True)
    max_share = max(shares, default=1.0)
    axis.set_xlim(0, max_share * 1.32)
    for position, share, count in zip(positions, shares, counts, strict=True):
        axis.text(
            share + max_share * 0.015,
            position,
            f"{share:.1f}%  ({count:,})",
            va="center",
            fontsize=9,
            color="#20242B",
        )
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.tick_params(axis="y", length=0)


def write_charts(
    result: dict[str, Any],
    output_dir: Path,
    *,
    top_n: int = 15,
) -> list[Path]:
    """Generate combined and facility-specific PNG/SVG origin charts."""

    if top_n <= 0:
        raise ValueError("top_n must be positive")
    import os
    import tempfile

    os.environ.setdefault(
        "MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "ppflow-matplotlib")
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in (
        "Hiragino Sans",
        "Yu Gothic",
        "Noto Sans CJK JP",
        "IPAexGothic",
    ):
        if candidate in available_fonts:
            plt.rcParams["font.family"] = candidate
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["svg.fonttype"] = "none"

    output_dir.mkdir(parents=True, exist_ok=True)
    colors = {
        "commercial": "#2F6BFF",
        "office": "#08A37A",
        "hotel": "#F28E2B",
    }
    paths: list[Path] = []

    facilities = list(result["facilities"].items())
    figure, axes = plt.subplots(len(facilities), 1, figsize=(13, 17))
    if len(facilities) == 1:
        axes = [axes]
    for axis, (key, facility) in zip(axes, facilities, strict=True):
        _plot_origin_bars(
            axis,
            facility,
            top_n=top_n,
            color=colors.get(key, "#4E79A7"),
        )
    destination = "・".join(result["scope"]["destination_zones"])
    figure.suptitle(
        f"中京PT 用途別出発中ゾーン分布（到着中ゾーン {destination}）",
        fontsize=19,
        fontweight="bold",
        y=0.995,
    )
    figure.text(
        0.01,
        0.006,
        "注: 現行OD表に到着施設列がないため、施設用途別目的集合による代理分布。値は拡大トリップ数。",
        fontsize=9,
        color="#5B6472",
    )
    figure.tight_layout(rect=(0, 0.025, 1, 0.982), h_pad=2.0)
    combined_base = output_dir / f"origin_distribution_top{top_n}_all_facilities"
    for suffix in (".png", ".svg"):
        path = combined_base.with_suffix(suffix)
        figure.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
        paths.append(path)
    plt.close(figure)

    for key, facility in facilities:
        figure, axis = plt.subplots(figsize=(12, 8.5))
        _plot_origin_bars(
            axis,
            facility,
            top_n=top_n,
            color=colors.get(key, "#4E79A7"),
        )
        figure.suptitle(
            f"中京PT 出発中ゾーン分布（到着中ゾーン {destination}）",
            fontsize=17,
            fontweight="bold",
            y=0.99,
        )
        figure.text(
            0.01,
            0.008,
            "注: 現行OD表に到着施設列がないため目的代理。割合は当該用途の全出発ゾーン合計に対する値。",
            fontsize=9,
            color="#5B6472",
        )
        figure.tight_layout(rect=(0, 0.035, 1, 0.96))
        base = output_dir / f"origin_distribution_top{top_n}_{key}"
        for suffix in (".png", ".svg"):
            path = base.with_suffix(suffix)
            figure.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
            paths.append(path)
        plt.close(figure)
    return paths

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="中京PTのOD表から施設用途別の出発中ゾーン分布を作成します。"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="中京PT OD CSV")
    parser.add_argument(
        "--destination-zone",
        action="append",
        default=None,
        help="対象の到着中ゾーン。複数指定可。省略時は名鉄名古屋周辺の5。",
    )
    parser.add_argument(
        "--zone-code-xlsx",
        type=Path,
        default=DEFAULT_ZONE_CODE_XLSX,
        help="中ゾーンコード表XLSX。存在すれば自治体ラベルを付与。",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--top-n",
        type=int,
        default=15,
        help="図と要約表へ表示する上位出発ゾーン数。省略時は15。",
    )
    parser.add_argument(
        "--skip-charts",
        action="store_true",
        help="Matplotlib図を生成せず、JSON・CSVだけを出力する。",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        raise FileNotFoundError(f"Input CSV not found: {args.input}")
    destination_zones = args.destination_zone or ["5"]
    zone_labels: dict[str, MiddleZoneInfo] = {}
    if args.zone_code_xlsx.exists():
        zone_labels = load_middle_zone_labels(args.zone_code_xlsx)
    result = build_origin_distributions(
        args.input,
        destination_zones,
        zone_labels=zone_labels,
    )
    summary_path = write_summary_table(result, args.output_dir, top_n=args.top_n)
    chart_paths = [] if args.skip_charts else write_charts(
        result, args.output_dir, top_n=args.top_n
    )
    result["outputs"] = {
        "summary_table": str(summary_path.resolve()),
        "charts": [str(path.resolve()) for path in chart_paths],
    }
    json_path, csv_path = write_outputs(result, args.output_dir)
    print(f"method: {result['method']}")
    print(f"destination zones: {', '.join(result['scope']['destination_zones'])}")
    for facility in result["facilities"].values():
        print(
            f"{facility['name']}: {facility['total_trips']:,} trips, "
            f"{facility['origin_zone_count']} origin zones"
        )
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(f"summary table: {summary_path}")
    for path in chart_paths:
        print(f"chart: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
