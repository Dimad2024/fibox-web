#!/usr/bin/env python3
"""
Look up Fibox sales contacts and distributors by country.
Usage: python3 contacts_lookup.py [country]
       (no argument = list all available countries)
"""
import sys, json, os, re

_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(_DIR, 'contacts.json')

_DROPDOWN_END = 'Yemen\n'


def load():
    with open(DATA, encoding='utf-8') as f:
        return json.load(f)


def fuzzy_match(query, keys):
    q = query.strip().lower()
    for k in keys:
        if k.lower() == q:
            return k
    for k in keys:
        if k.lower().startswith(q):
            return k
    for k in keys:
        if q in k.lower():
            return k
    return None


def is_email(s):
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$', s))

def is_phone(s):
    return bool(re.match(r'^[\+\d][\d\s\-\.\(\)]{5,}$', s.strip()))

def is_url(s):
    return bool(re.match(r'^(https?://|www\.)', s) or
                (re.match(r'^[\w\-]+\.[\w]{2,}', s) and '/' in s))

def is_title(s):
    # All-caps words with spaces/commas/& — job title
    return bool(s and s == s.upper() and re.search(r'[A-Z]{2}', s))


def parse_raw(raw):
    idx = raw.find(_DROPDOWN_END)
    body = raw[idx + len(_DROPDOWN_END):].strip() if idx >= 0 else raw.strip()

    lines = [l.strip() for l in body.splitlines() if l.strip()]

    sales        = []
    distributors = []
    section      = None
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.upper() == 'SALES':
            section = 'sales'
            i += 1
            continue
        if line.upper() == 'DISTRIBUTORS':
            section = 'distributors'
            i += 1
            continue

        # ── SALES contact block ────────────────────────────────────────────
        if section == 'sales':
            # First line is the name; collect following attribute lines
            name  = line
            title = ''
            email = ''
            phone = ''
            i += 1
            while i < len(lines):
                nxt = lines[i]
                # Stop at next section header or next person name
                if nxt.upper() in ('SALES', 'DISTRIBUTORS'):
                    break
                if is_email(nxt):
                    email = nxt; i += 1
                elif nxt.lower().startswith('tel.'):
                    phone = nxt[4:].strip(); i += 1
                elif is_phone(nxt) and not phone:
                    phone = nxt; i += 1
                elif is_title(nxt) and not title:
                    title = nxt; i += 1
                else:
                    # Looks like the start of the next person
                    break
            sales.append({'name': name, 'title': title,
                          'email': email, 'phone': phone})

        # ── DISTRIBUTOR block ──────────────────────────────────────────────
        elif section == 'distributors':
            name  = line
            url   = ''
            phone = ''
            email = ''
            addr_parts = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.upper() in ('SALES', 'DISTRIBUTORS'):
                    break
                if is_url(nxt) and not url:
                    url = nxt; i += 1
                elif is_email(nxt) and not email:
                    email = nxt; i += 1
                elif (nxt.lower().startswith('tel.') or is_phone(nxt)) and not phone:
                    phone = nxt.lstrip('Tt').lstrip('el.').strip() if nxt.lower().startswith('tel') else nxt
                    i += 1
                elif re.match(r'^[A-Z]{2}-?\d{4,5}', nxt) or re.match(r'^\d{4,6}\s', nxt):
                    # postal address line
                    addr_parts.append(nxt); i += 1
                elif is_title(nxt):
                    # Stray label — skip
                    i += 1
                else:
                    # Could be address or next distributor name — peek ahead
                    # If next line looks like a URL/phone/email, this is address
                    if i + 1 < len(lines) and (is_url(lines[i+1]) or is_phone(lines[i+1]) or is_email(lines[i+1])):
                        addr_parts.append(nxt); i += 1
                    else:
                        break
            dist = {'name': name}
            if url:   dist['url']   = url
            if email: dist['email'] = email
            if phone: dist['phone'] = phone
            if addr_parts: dist['address'] = ', '.join(addr_parts)
            distributors.append(dist)

        else:
            i += 1

    return {'sales': sales, 'distributors': distributors}


def main():
    db = load()

    if len(sys.argv) < 2:
        print(json.dumps({'countries': sorted(db.keys())}))
        return

    query   = ' '.join(sys.argv[1:])
    matched = fuzzy_match(query, db.keys())

    if not matched:
        print(json.dumps({
            'error': f'No contacts found for "{query}".',
            'available_countries': sorted(db.keys()),
        }))
        return

    entry  = db[matched]
    parsed = parse_raw(entry['raw'])
    print(json.dumps({
        'country'     : matched,
        'sales'       : parsed['sales'],
        'distributors': parsed['distributors'],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
