#!/usr/bin/env python3
"""
Search Fibox enclosures by dimension with +/-20% tolerance.
Usage: python3 search_enclosures.py <W_mm> <D_mm> <H_mm>
"""
import sys, json, re, os
import openpyxl

_THIS_FILE   = os.path.abspath(__file__)
_SCRIPTS_DIR = os.path.dirname(_THIS_FILE)
_APP_DIR     = os.path.dirname(_SCRIPTS_DIR)
DATA_FILE    = os.path.join(_APP_DIR, 'master_web.xlsx')

TOLERANCE    = 0.20
EXACT_THRESH = 0.02   # within 2% counts as an exact dimension match
COL_GROUP    = 0
COL_CODE     = 2
COL_SYMBOL   = 3
COL_DESC     = 4
COL_PACK     = 5
COL_DIM      = 6
COL_WEIGHT   = 7
COL_URL      = 10
DATA_START   = 3


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


def exact_dim_count(dims, target, thresh=EXACT_THRESH):
    """Count how many individual dimensions are within thresh% of target."""
    return sum(
        1 for v, t in zip(dims, target)
        if abs(v - t) / max(t, 1) <= thresh
    )


def search(ws, req):
    """Return sorted matches for the given (W, D, H) request tuple."""
    results = []
    for row in ws.iter_rows(min_row=DATA_START, values_only=True):
        if str(row[COL_GROUP] or '').strip().upper() == 'NEO':
            continue
        dims = parse_dim(row[COL_DIM])
        if dims is None:
            continue
        W, D, H = dims
        if (within_tolerance(W, req[0], TOLERANCE) and
            within_tolerance(D, req[1], TOLERANCE) and
            within_tolerance(H, req[2], TOLERANCE)):
            grp   = str(row[COL_GROUP] or '').strip().upper()
            vd    = round(volume_diff(dims, req), 4)
            exact = exact_dim_count(dims, req)
            mce   = 1 if grp == 'MCE' else 0
            results.append({
                'group'      : row[COL_GROUP],
                'code'       : row[COL_CODE],
                'symbol'     : row[COL_SYMBOL],
                'description': row[COL_DESC],
                'width_mm'   : W,
                'depth_mm'   : D,
                'height_mm'  : H,
                'dim_str'    : str(row[COL_DIM]),
                'pack_unit'  : row[COL_PACK],
                'weight_kg'  : row[COL_WEIGHT],
                'weblink'    : (str(row[COL_URL]) if row[COL_URL] and str(row[COL_URL]).strip() not in ('', '-', 'None') else ''),
                'vol_diff'   : vd,
                'exact_dims' : exact,
                '_sort'      : (mce, -exact, vd),
            })
    results.sort(key=lambda x: x['_sort'])
    for r in results:
        del r['_sort']
    return results


def main():
    if len(sys.argv) != 4:
        print(json.dumps({'error': 'Usage: search_enclosures.py W D H'}))
        sys.exit(1)
    try:
        req = tuple(float(a) for a in sys.argv[1:4])
    except ValueError:
        print(json.dumps({'error': 'W, D, H must be numbers (mm)'}))
        sys.exit(1)

    if not os.path.exists(DATA_FILE):
        print(json.dumps({'error': f'Data file not found: {DATA_FILE}'}))
        sys.exit(1)

    wb = openpyxl.load_workbook(DATA_FILE, data_only=True, read_only=True)
    ws = wb['PRODUCTS']

    # Primary search: W x D x H as requested
    primary = search(ws, req)
    primary_codes = {r['code'] for r in primary}

    # Swapped search: D x W x H (only meaningful when W != D)
    swapped = []
    if abs(req[0] - req[1]) > 1:          # skip if W ≈ D (square footprint)
        req_swap = (req[1], req[0], req[2])
        swapped = [r for r in search(ws, req_swap)
                   if r['code'] not in primary_codes]

    wb.close()
    print(json.dumps({
        'requested'       : {'W': req[0], 'D': req[1], 'H': req[2]},
        'count'           : len(primary),
        'matches'         : primary[:20],
        'swapped_requested': {'W': req[1], 'D': req[0], 'H': req[2]},
        'swapped_count'   : len(swapped),
        'swapped_matches' : swapped[:20],
    }))


if __name__ == '__main__':
    main()
