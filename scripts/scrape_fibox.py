#!/usr/bin/env python3
"""
Scrape product details and/or distributor info from fibox.com.
Usage:
  python3 scrape_fibox.py product <URL>
  python3 scrape_fibox.py distributors [country]
"""
import sys, json, time
import requests
from bs4 import BeautifulSoup

HEADERS  = {
    'User-Agent': 'Mozilla/5.0 (compatible; FiboxAgent/1.0)',
    'Accept-Language': 'en-US,en;q=0.9',
}
BASE_URL = 'https://www.fibox.com'

def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r.text
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    return None

def scrape_product(url):
    html = fetch(url)
    if not html:
        return {'error': f'Could not fetch {url}'}
    soup = BeautifulSoup(html, 'lxml')
    title = ''
    h1 = soup.find('h1')
    if h1:
        title = h1.get_text(strip=True)
    description = ''
    for cls in ['description', 'intro', 'product-intro']:
        elem = soup.find('div', class_=lambda c: c and cls in c.lower())
        if elem:
            description = elem.get_text(separator=' ', strip=True)
            break
    features = []
    for ul in soup.find_all('ul'):
        items = [li.get_text(strip=True) for li in ul.find_all('li') if li.get_text(strip=True)]
        if 3 <= len(items) <= 20:
            features.extend(items)
            break
    specs = {}
    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cols = row.find_all(['td', 'th'])
            if len(cols) == 2:
                k = cols[0].get_text(strip=True)
                v = cols[1].get_text(strip=True)
                if k and v:
                    specs[k] = v
    return {'url': url, 'title': title,
            'description': description[:800],
            'features': features[:15], 'specs': specs}

def scrape_distributors(country_filter=None):
    # fibox.com is a JavaScript SPA — the distributor locator cannot be scraped directly.
    # Direct the customer to the website instead.
    country_str = f' in {country_filter}' if country_filter else ''
    return {
        'info': (
            f'To find a Fibox distributor{country_str}, visit https://www.fibox.com '
            'and use the "Sales Network" or "Where to Buy" section. '
            'The interactive locator lets you filter by country to find your nearest distributor.'
        ),
        'url': 'https://www.fibox.com',
    }

def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: scrape_fibox.py product <URL> | distributors [country]'}))
        sys.exit(1)
    command = sys.argv[1].lower()
    if command == 'product' and len(sys.argv) >= 3:
        print(json.dumps(scrape_product(sys.argv[2])))
    elif command == 'distributors':
        country = sys.argv[2] if len(sys.argv) > 2 else None
        print(json.dumps(scrape_distributors(country)))
    else:
        print(json.dumps({'error': f'Unknown command: {sys.argv[1]}'}))

if __name__ == '__main__':
    main()
