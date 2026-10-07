"""Generate synthetic datasheets and an exact duplicate; no personal data is used."""
import csv
import json
import shutil
from pathlib import Path
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
ROWS = [
    ["W-01", "West", "2025-01-05", 1000, 10, 200],
    ["W-02", "West", "2025-01-20", 500, 5, 100],
    ["W-03", "West", "2025-02-05", 1800, 18, 360],
    ["W-04", "West", "2025-02-20", 300, 3, 60],
    ["E-01", "East", "2025-01-10", 800, 8, 160],
    ["E-02", "East", "2025-02-10", 600, 6, 120],
]
HEADER = ["order_id", "region", "day", "amount", "units", "profit"]


def generate(destination=None):
    root = Path(destination) if destination else ROOT / "samples"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "sales.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(ROWS)
    shutil.copyfile(root / "sales.csv", root / "sales-duplicate.csv")
    book = Workbook()
    sheet = book.active
    sheet.title = "Sales"
    sheet.append(HEADER)
    for row in ROWS:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 16
    copied = book.copy_worksheet(sheet)
    copied.title = "Sales duplicate"
    book.save(root / "sales-with-duplicate-sheet.xlsx")
    expected = {"total_amount": 5000, "total_units": 50, "total_profit": 1000,
        "profit_percentage": 20, "amount_per_unit": 100,
        "growth_by_region": {"West": {"baseline": 1500, "current": 2100, "change": 600, "value": 40},
            "East": {"baseline": 800, "current": 600, "change": -200, "value": -25}}}
    (root / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    return root


if __name__ == "__main__":
    print(generate())
