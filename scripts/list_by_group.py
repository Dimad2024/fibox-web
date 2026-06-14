#!/usr/bin/env python3
"""
List all Fibox products in a given group/family.
Usage: python3 list_by_group.py <GROUP> [keyword]

GROUP   - product family name, e.g. ARCA, MNX, CAB, EURONORD
keyword - optional filter applied to category, symbol, or description
          (case-insensitive substring match)
          If the keyword returns 0 results, falls back to showing all in the group.
"""
import sys, json, os
import openpyxl

_THIS_FILE   = os.path.abspath(__file__)
_SCRIPTS_DIR = os.path.dirname(_THIS_FILE)
_APP_DIR     = os.path.dirname(_SCRIPTS_DIR)
DATA_FILE    = os.path.join(_APP_DIR, 'master_web.xlsx')

# Column indices (0-based) in the PRODUCTS sheet
COL_GROUP  = 0
COL_CAT    = 1
COL_CODE   = 2
COL_SYMBOL = 3
COL_DESC   = 4
COL_PACK   = 5
COL_DIM    = 6
COL_WEIGHT = 7
COL_URL    = 10
DATA_START  = 3   # first data row (1-based); rows 1-2 are headers


def row_matches_keyword(row, keyword):
    """Check if keyword appears in category, symbol, or description."""
    kw = keyword.upper()
    fields = [row[COL_CAT], row[COL_SYMBOL], row[COL_DESC]]
    return any(kw in str(f or '').upper() for f in fields)


def load_rows(group_filter, category_filter=''):
    """Load rows matching group, optionally filtered by keyword."""
    group_upper = group_filter.upper()
    wb = openpyxl.load_workbook(DATA_FILE, data_only=True, read_only=True)
    ws = wb['PRODUCTS']
    results = []

    for row in ws.iter_rows(min_row=DATA_START, values_only=True):
        row_group = str(row[COL_GROUP] or '').upper()
        if row_group == 'NEO':
            continue
        if row_group != group_upper:
            continue

        # Skip accessory rows unless caller explicitly wants accessories
        cat = str(row[COL_CAT] or '').upper()
        want_accessories = 'ACCESSOR' in category_filter.upper()
        if 'ACCESSOR' in cat and not want_accessories:
            continue

        # Apply optional keyword filter
        if category_filter and not row_matches_keyword(row, category_filter):
            continue

        results.append({
            'group'      : row[COL_GROUP],
            'category'   : row[COL_CAT],
            'code'       : row[COL_CODE],
            'symbol'     : row[COL_SYMBOL],
            'description': row[COL_DESC],
            'pack_unit'  : row[COL_PACK],
            'dim_str'    : str(row[COL_DIM] or ''),
            'weight_kg'  : row[COL_WEIGHT],
            'weblink'    : str(row[COL_URL] or ''),
        })

    wb.close()
    return results


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: list_by_group.py GROUP [keyword]'}))
        sys.exit(1)

    group_filter    = sys.argv[1]
    category_filter = sys.argv[2] if len(sys.argv) >= 3 else ''

    results = load_rows(group_filter, category_filter)

    # Fallback: if keyword filter returned nothing, retry without it
    filtered = bool(category_filter and not results)
    if filtered:
        results = load_rows(group_filter, '')

    print(json.dumps({
        'group'         : group_filter,
        'keyword'       : category_filter,
        'fallback_used' : filtered,
        'count'         : len(results),
        'products'      : results,
    }))


if __name__ == '__main__':
    main()
