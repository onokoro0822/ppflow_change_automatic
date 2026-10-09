"""Convert sales floor area to a provisional retail employee increment.

Inputs are transcribed from the 2014 Census of Commerce, yearbook table 14-5.
This is a proxy: temporary employees are excluded in this source.
"""
import argparse
import json
import math
from pathlib import Path

DEFAULT_SOURCE = Path(__file__).resolve().parent / "config/retail_floor_area_2014.json"


def employee_increment(area_m2, employees, source_area_1000m2):
    if not math.isfinite(area_m2) or area_m2 < 0:
        raise ValueError("sales floor area must be finite and non-negative")
    if not math.isfinite(employees) or employees <= 0:
        raise ValueError("source employees must be finite and positive")
    if not math.isfinite(source_area_1000m2) or source_area_1000m2 <= 0:
        raise ValueError("source area must be finite and positive")
    return area_m2 * employees / (source_area_1000m2 * 1000)


def calculate(source, area_m2):
    rows = []
    for row in source["rows"]:
        density = employee_increment(1, row["employees"], row["sales_floor_area_1000m2"])
        rows.append({**row, "employees_per_m2": density,
                     "m2_per_employee": 1 / density,
                     "mesh_capacity_add": employee_increment(
                         area_m2, row["employees"], row["sales_floor_area_1000m2"])})
    return {"source_url": source["source_url"], "source_sha256": source["source_sha256"],
            "status": source["status"], "sales_floor_area_m2": area_m2,
            "selected_category": source["selected_category"], "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--sales-floor-area-m2", type=float)
    args = parser.parse_args()
    source = json.loads(args.source.read_text())
    area = args.sales_floor_area_m2 if args.sales_floor_area_m2 is not None else source["sales_floor_area_m2"]
    print(json.dumps(calculate(source, area), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
