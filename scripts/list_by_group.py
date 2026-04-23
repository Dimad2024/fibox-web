#!/usr/bin/env python3
"""
List all Fibox products for a given product group.
Usage: python3 list_by_group.py <group_name> [category_filter]
"""
import sys, json, re, os
import openpyxl

_THIS_FILE   = os.path.abspath(__file__)
_SCRIPTS_DIR = os.path.dirname(_THIS_FILE)
_APP_DIR     = os.path.dirname(_SCRIPTS_DIR)
DATA_FILE    = os.path.join(_APP_DIR, 'master_web.xlsx')

COL_GROUP  = 0
COL_CAT    = 1
COL_CODE   = 2
COL_SYMBOL = 3
COL_DESC   = 4
COL_PACK   = 5
COL_DIM    = 6
COL_WEIGHT = 7
COL_URL    = 10
DATA_START = 3

def parse_dim(dim_str):
    if not dim_str:
        return (0, 0, 0)
    nums = re.findall(r'[\d]+(?:[.,][\d]+)?', str(dim_str))
    if len(nums) >= 3:
        try:
            return tuple(float(n.replace(',', '.')) for n in nums[:3])
        except ValueError:
            pass
    return (0, 0, 0)

def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: list_by_group.py <group> [category_keyword]'}))
        sys.exit(1)

    group_filter    = sys.argv[1].strip().upper()
    category_filter = sys.argv[2].strip().upper() if len(sys.argv) > 2 else ''

    if not os.path.exists(DATA_FILE):
        print(json.dumps({'error': f'Data file not found: {DATA_FILE}'}))
        sys.exit(1)

    wb = openpyxl.load_workbook(DATA_FILE, data_only=True, read_only=True)
    ws = wb['PRODUCTS']
    results = []

    for row in ws.iter_rows(min_row=DATA_START, values_only=True):
        grp = str(row[COL_GROUP] or '').strip().upper()
        cat = str(row[COL_CAT]   or '').strip().upper()
        if grp != group_filter:
            continue
        if category_filter and category_filter not in cat:
            continue
        # Skip pure accessories rows
        if 'ACCESSOR' in cat:
            continue
        dims = parse_dim(row[COL_DIM])
        results.append({
            'group'      : row[COL_GROUP],
            'category'   : row[COL_CAT],
            'code'       : row[COL_CODE],
            'symbol'     : row[COL_SYMBOL],
            'description': row[COL_DESC],
            'width_mm'   : dims[0],
            'depth_mm'   : dims[1],
            'height_mm'  : dims[2],
            'dim_str'    : str(row[COL_DIM] or ''),
            'pack_unit'  : row[COL_PACK],
            'weight_kg'  : row[COL_WEIGHT],
            'weblink'    : str(row[COL_URL] or ''),
        })

    # Sort by dimensions: W, D, H
    results.sort(key=lambda x: (x['width_mm'], x['depth_mm'], x['height_mm']))

    wb.close()
    print(json.dumps({
        'group'  : sys.argv[1],
        'count'  : len(results),
        'products': results[:60]
    }))

if __name__ == '__main__':
    main()
