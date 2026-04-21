#!/usr/bin/env python3
"""
Search Fibox enclosures by dimension with +/-20% tolerance.
Reads master_web.xlsx — the web-safe deployment catalogue.
Usage:  python3 search_enclosures.py <W_mm> <D_mm> <H_mm>
Example: python3 search_enclosures.py 300 250 150
"""
import sys, json, re, os
import openpyxl

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..', '..'))
DATA_FILE    = os.path.join(PROJECT_ROOT, 'master_web.xlsx')
TOLERANCE    = 0.20   # 20% — change to 0.10 or 0.30 to adjust search width
PRIORITY_GROUPS = {'TEMPO', 'MNX', 'EURONORD'}  # shown second when within tolerance
# ARCA always ranks first among non-exact matches

# ── COLUMN INDICES (0-based; header row 2, data from row 3) ───────────────────
COL_GROUP  = 0   # A: Product Group
COL_CODE   = 2   # C: Code
COL_SYMBOL = 3   # D: Symbol
COL_DESC   = 4   # E: Description
COL_PACK   = 5   # F: Packing Unit
COL_DIM    = 6   # G: Dimension (mm)
COL_WEIGHT = 7   # H: Weight (kg)
COL_URL    = 10  # K: Weblink
DATA_START = 3   # first data row (1-based)

def parse_dim(dim_str):
    if not dim_str:
        return None
    nums = re.findall(r'[\d]+(?:[.,][\d]+)?', str(dim_str))
    if len(nums) >= 3:
        try:
            return tuple(float(n.replace(',', '.')) for n in nums[:3])
        except ValueError:
            return None
    return None

def within_tolerance(val, target, tol):
    return target * (1 - tol) <= val <= target * (1 + tol)

def volume_diff(dims, target_dims):
    vol   = dims[0] * dims[1] * dims[2]
    vol_t = target_dims[0] * target_dims[1] * target_dims[2]
    return abs(vol - vol_t) / max(vol_t, 1)

def main():
    if len(sys.argv) != 4:
        print(json.dumps({'error': 'Usage: search_enclosures.py W D H'}))
        sys.exit(1)
    try:
        req = tuple(float(a) for a in sys.argv[1:4])
    except ValueError:
        print(json.dumps({'error': 'W, D, H must be numbers (mm)'}))
        sys.exit(1)

    wb = openpyxl.load_workbook(DATA_FILE, data_only=True, read_only=True)
    ws = wb['PRODUCTS']
    results = []

    seen = set()
    for row in ws.iter_rows(min_row=DATA_START, values_only=True):
        dims = parse_dim(row[COL_DIM])
        if dims is None:
            continue
        W, D, H = dims
        # Try both W×D and D×W orientations
        for w, d, label in [(W, D, False), (D, W, True)]:
            if (within_tolerance(w, req[0], TOLERANCE) and
                within_tolerance(d, req[1], TOLERANCE) and
                within_tolerance(H, req[2], TOLERANCE)):
                key = (row[COL_SYMBOL], w, d, H)
                if key in seen:
                    continue
                seen.add(key)
                entry = {
                    'group'      : row[COL_GROUP],
                    'code'       : str(row[COL_CODE] or ''),
                    'symbol'     : row[COL_SYMBOL],
                    'description': row[COL_DESC],
                    'width_mm'   : w,
                    'depth_mm'   : d,
                    'height_mm'  : H,
                    'dim_str'    : str(row[COL_DIM]),
                    'pack_unit'  : row[COL_PACK],
                    'weight_kg'  : row[COL_WEIGHT],
                    'weblink'    : str(row[COL_URL] or ''),
                    'vol_diff'   : round(volume_diff((w, d, H), req), 4),
                }
                if label:
                    entry['note'] = 'W/D swapped to match'
                results.append(entry)
                break  # avoid adding same product twice if both orientations match

    def sort_key(x):
        if x['vol_diff'] == 0:
            return (0, 0, 0)
        if x['group'] == 'ARCA':
            return (1, 0, x['vol_diff'])
        if x['group'] in PRIORITY_GROUPS:
            return (1, 1, x['vol_diff'])
        return (2, 0, x['vol_diff'])
    results.sort(key=sort_key)
    print(json.dumps({
        'requested': {'W': req[0], 'D': req[1], 'H': req[2]},
        'count'    : len(results),
        'matches'  : results[:20]
    }))

if __name__ == '__main__':
    main()
