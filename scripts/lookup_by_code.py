#!/usr/bin/env python3
"""Look up a Fibox product by its numeric code."""
import sys, json, os
import openpyxl

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX_PATH = os.path.join(BASE_DIR, "..", "master_web.xlsx")
if not os.path.exists(XLSX_PATH):
    XLSX_PATH = os.path.join(BASE_DIR, "master_web.xlsx")

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: lookup_by_code.py <code>"}))
        sys.exit(1)

    query = sys.argv[1].strip().lower()

    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    ws = wb["PRODUCTS"]

    results = []
    for row in ws.iter_rows(min_row=3, values_only=True):
        code = str(row[2] or "").strip().lower()
        if code == query:
            results.append({
                "group":       str(row[0] or ""),
                "category":    str(row[1] or ""),
                "code":        str(row[2] or ""),
                "symbol":      str(row[3] or ""),
                "description": str(row[4] or ""),
                "pack_unit":   row[5],
                "dim_str":     str(row[6] or ""),
                "weight_kg":   row[7],
                "weblink":     str(row[10] or ""),
            })

    if not results:
        print(json.dumps({"error": f"No product found with code {sys.argv[1]}"}))
    else:
        print(json.dumps({"count": len(results), "results": results}))

if __name__ == "__main__":
    main()
