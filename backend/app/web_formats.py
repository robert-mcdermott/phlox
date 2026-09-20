"""PDF/JSON extraction orchestration over already downloaded, bounded bytes."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit

from app.web_fetch import FetchError, Page, select_passage

_SLOTS = threading.BoundedSemaphore(2)


def validate(pdf_page=None, json_pointer='', json_start=0, json_limit=20):
    if pdf_page is not None and (type(pdf_page) is not int or not 1 <= pdf_page <= 10000):
        raise FetchError('invalid_selection', 'pdf_page must be a one-based page number from 1 to 10,000.')
    if (not isinstance(json_pointer, str) or len(json_pointer) > 512
            or (json_pointer and not json_pointer.startswith('/')) or re.search(r'~(?![01])', json_pointer)):
        raise FetchError('invalid_selection', 'json_pointer must be an RFC 6901 pointer of at most 512 characters, or empty for root.')
    if type(json_start) is not int or not 0 <= json_start <= 2_097_152:
        raise FetchError('invalid_selection', 'json_start must be a nonnegative array index within the download bound.')
    if type(json_limit) is not int or not 1 <= json_limit <= 50:
        raise FetchError('invalid_selection', 'json_limit must be between 1 and 50 complete array items.')


def extract(body, format, deadline, **options):
    deadline.check()
    if not _SLOTS.acquire(blocking=False):
        raise FetchError('extractor_busy', 'PDF/JSON extractors are busy. Try again later.')
    process = None
    try:
        payload = json.dumps({'body': base64.b64encode(body).decode(), 'format': format, **options}).encode()
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('web_extract_worker.py'))],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        while True:
            deadline.check()
            try:
                output, _ = process.communicate(input=payload, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                payload = None
        deadline.check()
        if process.returncode or len(output) > 4 * 1024 * 1024:
            raise FetchError('extraction_limit', 'PDF/JSON extraction failed or exceeded process resource limits. No evidence captured.')
        result = json.loads(output)
        if result.get('error'):
            raise FetchError(result['error'], result['message'])
        return result['result']
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FetchError('extraction_failed', 'PDF/JSON extraction could not complete. No evidence captured.') from exc
    finally:
        if process:
            if process.poll() is None:
                process.kill()
            process.communicate()
        _SLOTS.release()


def page(body, format, url, status, deadline, *, query, start_char, max_chars,
         pdf_page=None, json_pointer='', json_start=0, json_limit=20):
    if format == 'json' and (query or start_char or pdf_page is not None):
        raise FetchError('invalid_selection', 'For JSON use json_pointer/json_start/json_limit, not query/start_char/pdf_page.')
    if format == 'pdf' and (json_pointer or json_start or json_limit != 20):
        raise FetchError('invalid_selection', 'JSON selectors cannot be used for a PDF.')
    result = extract(body, format, deadline, pdf_page=pdf_page, json_pointer=json_pointer,
                     json_start=json_start, json_limit=json_limit, max_chars=min(max_chars, 6000))
    digest = hashlib.sha256(body).hexdigest()
    filename = re.sub(r'\s+', ' ', unquote(urlsplit(url).path.rsplit('/', 1)[-1]))[:400]
    title = f'{format.upper()}: {filename}' if filename else f'{format.upper()} response'
    if format == 'json':
        text = result['text']
        return Page(url, title, text, digest, result['truncated'], status,
                    total_chars=len(text), passages=[{'text': text, 'start_char': 0,
                    'total_chars': len(text), 'provenance': result['location']}], notice=result['notice'])
    pages = result['pages']
    full = '\n\n'.join(p['text'] for p in pages)
    excerpt, start = select_passage(full, query, start_char, max_chars, deadline)
    passages, offset = [], 0
    for item in pages:
        lo, hi = max(0, start - offset), min(len(item['text']), start + len(excerpt) - offset)
        if hi > lo:
            passages.append({'text': item['text'][lo:hi], 'start_char': lo, 'total_chars': len(item['text']),
                             'provenance': {'format': 'pdf', 'page': item['page'], 'page_count': result['page_count']}})
        offset += len(item['text']) + 2
    if not passages:
        raise FetchError('selection_empty', 'Selection contains no PDF page text. Choose another offset or page.')
    notice = (f"PDF: {result['page_count']} pages. Layout text preserves basic columns; verify complex tables against the original. "
              + (f'Selected pdf_page={pdf_page}. ' if pdf_page else '')
              + (f"Pages without extractable text (OCR not performed): {result['empty_pages']}. " if result['empty_pages'] else ''))
    return Page(url, title, excerpt, digest, len(excerpt) < len(full) or bool(pdf_page), status,
                start, len(full), passages=passages, notice=notice)
