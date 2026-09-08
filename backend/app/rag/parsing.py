"""Bounded extraction retaining real PDF pages and document sections (no invented pages)."""
import hashlib
from pathlib import Path

PARSER_VERSION = '2'
CHUNKER_VERSION = '2'
MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 500
MAX_CHARS = 2_000_000
MAX_CHUNKS = 2500
TEXT_EXTS = {'.txt', '.md', '.markdown', '.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.csv',
             '.html', '.xml', '.yaml', '.yml', '.css', '.sql', '.sh', '.r', '.rst', '.log'}


class DocumentError(ValueError):
    pass


def extract(path: Path, mime=None, check=lambda: None):
    if path.stat().st_size > MAX_BYTES:
        raise DocumentError('Document exceeds the 20 MiB limit.')
    ext = path.suffix.lower()
    sections = []
    chars = 0

    def add(text, **location):
        nonlocal chars
        check()
        chars += len(text)
        if chars > MAX_CHARS:
            raise DocumentError('Extracted text exceeds the 2 million character limit.')
        if text.strip():
            sections.append((text, location))
    if ext == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if len(reader.pages) > MAX_PAGES:
            raise DocumentError('PDF exceeds the 500 page limit.')
        for page, item in enumerate(reader.pages, 1):
            check()
            add(item.extract_text() or '', page=page)
    elif ext == '.docx':
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        import zipfile
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 80 * 1024 * 1024:
                raise DocumentError('DOCX expanded contents exceed the 80 MiB limit.')
        doc = Document(path)
        heading = None
        table_number = 0
        for item in doc.iter_inner_content():
            if isinstance(item, Paragraph):
                if item.style and item.style.name.startswith('Heading'):
                    heading = item.text
                add(item.text, section=heading)
            elif isinstance(item, Table):
                table_number += 1
                for number, row in enumerate(item.rows, 1):
                    add(' | '.join(cell.text for cell in row.cells), section=heading, table=table_number, table_row=number)
    elif ext in TEXT_EXTS or (mime or '').startswith('text/'):
        try:
            text = path.read_text(encoding='utf-8-sig', errors='strict')
        except UnicodeDecodeError:
            raise DocumentError('Text files must use UTF-8 encoding.') from None
        if '\x00' in text:
            raise DocumentError('Binary files are unsupported. Upload PDF, DOCX, or UTF-8 text.')
        heading = None
        if ext in {'.md', '.markdown'}:
            buffer = []
            fence = None
            for line in text.splitlines(keepends=True):
                stripped = line.lstrip()
                if stripped.startswith(('```', '~~~')):
                    marker = stripped[:3]
                    fence = None if fence == marker else (marker if fence is None else fence)
                if fence is None and line.startswith('#') and line.lstrip('#').startswith(' '):
                    add(''.join(buffer), section=heading)
                    buffer = []
                    heading = line.lstrip('#').strip()
                buffer.append(line)
            add(''.join(buffer), section=heading)
        else:
            add(text)
    else:
        raise DocumentError('Unsupported file type. Upload PDF, DOCX, or UTF-8 text/code.')
    if not sections:
        raise DocumentError('No extractable text. Scanned PDFs need a text layer; OCR is not available.')
    return sections


def chunks(path, mime=None, check=lambda: None):
    if path.stat().st_size > MAX_BYTES:
        raise DocumentError('Document exceeds the 20 MiB limit.')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    out = []
    for text, location in extract(path, mime, check):
        for start in range(0, len(text), 1050):
            check()
            passage = text[start:start + 1200]
            if not passage.strip():
                continue
            out.append((passage, {**location, 'start': start, 'end': start + len(passage),
                                  'parser_version': PARSER_VERSION, 'chunker_version': CHUNKER_VERSION,
                                  'document_hash': digest}))
            if len(out) > MAX_CHUNKS:
                raise DocumentError('Document exceeds the 2500 chunk limit.')
            if start + 1200 >= len(text):
                break
    return out, digest
