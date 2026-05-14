#!/usr/bin/env python3
"""
List and read Fibox product PDFs from the docs/ folder.
Usage:
  python3 read_doc.py list               — list available documents
  python3 read_doc.py read <filename>    — return full text of a doc
"""
import sys, json, os
import pdfplumber

_DIR  = os.path.dirname(os.path.abspath(__file__))
DOCS  = os.path.join(_DIR, '..', 'docs')


def list_docs():
    files = [f for f in os.listdir(DOCS) if f.lower().endswith('.pdf')]
    return [{'filename': f, 'title': os.path.splitext(f)[0].replace('_', ' ').replace('-', ' ')}
            for f in sorted(files)]


def read_doc(filename):
    # Safety: strip any path traversal
    filename = os.path.basename(filename)
    path = os.path.join(DOCS, filename)
    if not os.path.exists(path):
        return {'error': f'Document not found: {filename}'}
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ''
            if text.strip():
                pages.append({'page': i, 'text': text.strip()})
    full_text = '\n\n'.join(p['text'] for p in pages)
    return {
        'filename': filename,
        'pages'   : len(pages),
        'text'    : full_text[:40000],   # cap at ~40k chars to stay within context
    }


def extract_pdf_bytes(data: bytes) -> str:
    """Extract text from raw PDF bytes (for user-uploaded PDFs)."""
    import io
    text_parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t and t.strip():
                text_parts.append(t.strip())
    return '\n\n'.join(text_parts)[:20000]


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: read_doc.py list | read <filename>'}))
        return

    cmd = sys.argv[1].lower()
    if cmd == 'list':
        print(json.dumps({'documents': list_docs()}))
    elif cmd == 'read' and len(sys.argv) >= 3:
        print(json.dumps(read_doc(sys.argv[2]), ensure_ascii=False))
    else:
        print(json.dumps({'error': 'Unknown command. Use: list | read <filename>'}))


if __name__ == '__main__':
    main()
