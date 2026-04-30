#!/usr/bin/env python3
"""
Scrape Fibox contact-us page for all countries using Playwright.
Outputs contacts.json in the same directory as this script.

Install deps (once):
  pip install playwright
  playwright install chromium

Run:
  python3 scrape_contacts.py
"""
import json, time, os
from playwright.sync_api import sync_playwright

OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'contacts.json')
URL      = 'https://www.fibox.com/contact-us'

def scrape():
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()
        page.goto(URL, wait_until='networkidle', timeout=30000)
        time.sleep(1)

        # Find the country select dropdown
        select = page.query_selector('select')
        if not select:
            print('ERROR: could not find <select> on page')
            browser.close()
            return {}

        # Get all country options (skip the placeholder "Select country")
        options = select.query_selector_all('option')
        countries = []
        for opt in options:
            val  = opt.get_attribute('value') or ''
            text = opt.inner_text().strip()
            if val and text and text.lower() not in ('select country', ''):
                countries.append((val, text))

        print(f'Found {len(countries)} countries')

        for val, country_name in countries:
            print(f'  Scraping: {country_name}')
            try:
                # Select the country
                select.select_option(val)
                time.sleep(1.2)   # wait for dynamic content to load

                # Grab all visible text in the contacts content area
                # Try common container selectors
                content = ''
                for sel in [
                    '.contact-content', '.contact-result', '#contact-result',
                    '.contacts-info', '.country-contact', 'main', '.page-content',
                ]:
                    el = page.query_selector(sel)
                    if el:
                        content = el.inner_text().strip()
                        if len(content) > 40:
                            break

                # Fallback: grab everything below the select
                if not content or len(content) < 40:
                    body = page.query_selector('body')
                    content = body.inner_text().strip() if body else ''

                # Parse lines into structured data
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                results[country_name] = {
                    'country': country_name,
                    'raw': '\n'.join(lines),
                }

                # Try to extract email, phone, website
                import re
                emails   = re.findall(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', content)
                phones   = re.findall(r'[+]?[\d\s\-().]{7,20}', content)
                websites = re.findall(r'https?://[^\s"\'<>]+', content)

                if emails:   results[country_name]['emails']   = list(set(emails))
                if phones:   results[country_name]['phones']   = [p.strip() for p in phones[:5]]
                if websites: results[country_name]['websites'] = list(set(websites))

            except Exception as e:
                results[country_name] = {'country': country_name, 'error': str(e)}

        browser.close()

    return results


def main():
    print(f'Scraping {URL} ...')
    data = scrape()
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'\nDone. {len(data)} countries saved to {OUT_FILE}')


if __name__ == '__main__':
    main()
