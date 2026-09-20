"""Bounded format extraction subprocess. No network, app configuration or database access."""
import base64
from io import BytesIO
import json
import logging
import sys

MAX_TEXT = 500_000
MAX_PAGES = 200
MAX_DEPTH = 64


class ExtractionError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class Number(str):
    """Keep the original JSON number spelling; never round evidence through a float."""


def encode_json(value, depth=0):
    if isinstance(value, Number):
        yield str(value)
    elif isinstance(value, (dict, list)):
        is_object = isinstance(value, dict)
        yield '{' if is_object else '['
        for index, (key, item) in enumerate(value.items() if is_object else enumerate(value)):
            yield (',\n' if index else '\n') + '  ' * (depth + 1)
            if is_object:
                yield json.dumps(key, ensure_ascii=False) + ': '
            yield from encode_json(item, depth + 1)
        if value:
            yield '\n' + '  ' * depth
        yield '}' if is_object else ']'
    else:
        yield json.dumps(value, ensure_ascii=False, allow_nan=False)


def pdf(body, page_number):
    from pypdf import PdfReader
    from pypdf import filters
    filters.JBIG2DEC_BINARY = None  # Text extraction must never launch an external image decoder.

    # These controls require the pinned minimum pypdf version. They apply only in this
    # disposable process, and bound expansion on platforms without address-space limits.
    for name in ('ZLIB_MAX_OUTPUT_LENGTH', 'LZW_MAX_OUTPUT_LENGTH', 'RUN_LENGTH_MAX_OUTPUT_LENGTH',
                 'MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH', 'JBIG2_MAX_OUTPUT_LENGTH', 'MAX_DECLARED_STREAM_LENGTH'):
        setattr(filters, name, 8 * 1024 * 1024)
    original_decode = filters.decode_stream_data
    decoded_bytes = 0

    def bounded_decode(stream):
        nonlocal decoded_bytes
        try:
            data = original_decode(stream)
        except filters.LimitReachedError as exc:
            raise ExtractionError('extraction_limit', 'PDF stream exceeds extraction expansion limits.') from exc
        decoded_bytes += len(data)
        if decoded_bytes > 32 * 1024 * 1024:
            raise ExtractionError('extraction_limit', 'PDF decoded streams exceed the 32 MiB extraction allowance.')
        return data

    filters.decode_stream_data = bounded_decode

    if not body.startswith(b'%PDF-'):
        raise ExtractionError('malformed_pdf', 'Response is not a valid PDF file.')
    reader = PdfReader(BytesIO(body), strict=True)
    if reader.is_encrypted:
        raise ExtractionError('encrypted_pdf', 'Encrypted PDFs are unsupported; use an unencrypted public copy.')
    count = len(reader.pages)
    if page_number and page_number > count:
        raise ExtractionError('selection_empty', f'PDF has {count} pages; pdf_page is out of range.')
    if not page_number and count > MAX_PAGES:
        raise ExtractionError('selection_empty', f'PDF has {count} pages, beyond the {MAX_PAGES}-page scan limit. Select pdf_page explicitly.')
    pages, empty, length = [], [], 0
    for i in ([page_number - 1] if page_number else range(count)):
        text = reader.pages[i].extract_text(extraction_mode='layout').strip()
        length += len(text)
        if length > MAX_TEXT:
            raise ExtractionError('selection_empty', 'PDF extraction exceeds 500,000 characters. Select a single pdf_page.')
        if text:
            pages.append({'page': i + 1, 'text': text})
        else:
            empty.append(i + 1)
    if not pages:
        raise ExtractionError('ocr_required', 'No extractable PDF text. Scanned/image-only pages require OCR, which web fetch does not perform.')
    return {'pages': pages, 'page_count': count, 'empty_pages': empty}


def pointer_part(value):
    return value.replace('~', '~0').replace('/', '~1')


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def structured(body, pointer, start, limit, max_chars):
    def reject_constant(value):
        raise ValueError('Nonstandard JSON constant')

    value = json.loads(body, object_pairs_hook=object_pairs, parse_constant=reject_constant,
                       parse_int=Number, parse_float=Number)
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise ExtractionError('malformed_json', 'JSON nesting exceeds 64 levels.')
        if isinstance(item, (dict, list)):
            stack.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))
    if pointer:
        for segment in pointer[1:].split('/'):
            key = segment.replace('~1', '/').replace('~0', '~')
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and key.isascii() and key.isdigit() and str(int(key)) == key and int(key) < len(value):
                value = value[int(key)]
            else:
                raise ExtractionError('selection_empty', 'JSON pointer does not identify a value in this response.')
    def render(item):
        parts, size = [], 0
        for part in encode_json(item):
            size += len(part)
            if size > max_chars:
                return None
            parts.append(part)
        return ''.join(parts)

    location = {'format': 'json', 'json_pointer': pointer}
    notice = 'Complete JSON value at pointer ' + json.dumps(pointer) + ' (empty pointer means root).'
    if isinstance(value, list):
        if start and start >= len(value):
            raise ExtractionError('selection_empty', f'JSON array has {len(value)} items; json_start is out of range.')
        selected = []
        for item in value[start:start + limit]:
            if render(selected + [item]) is None:
                break
            selected.append(item)
        text = render(selected)
        if text is None:
            raise ExtractionError('selection_empty', 'max_chars is too small for a complete JSON array.')
        if not selected and value:
            raise ExtractionError('selection_empty', 'A complete array item exceeds the passage limit. Select a smaller value with json_pointer, including its array index.')
        end = start + len(selected)
        location.update(item_start=start, item_end=end, total_items=len(value))
        notice = (f'JSON array at pointer {json.dumps(pointer)}: complete items [{start}, {end}) of {len(value)}. '
                  + (f'Other items omitted. Use json_start={end} for the next selection.' if end < len(value) else 'End of array.'))
        return {'text': text, 'location': location, 'notice': notice, 'truncated': bool(pointer) or start > 0 or end < len(value)}
    if start:
        raise ExtractionError('invalid_selection', 'json_start applies only to arrays.')
    text = render(value)
    if text is None:
        hints = []
        if isinstance(value, dict):
            for key, item in list(value.items())[:24]:
                path = pointer + '/' + pointer_part(key)
                kind = f'array ({len(item)} items)' if isinstance(item, list) else 'object' if isinstance(item, dict) else 'number' if isinstance(item, Number) else type(item).__name__
                hints.append(f'{json.dumps(path[:512])}: {kind}')
        raise ExtractionError('selection_empty', 'JSON value exceeds the passage limit; select a smaller json_pointer. '
                              'Structure preview only, not captured evidence: ' + '; '.join(hints))
    return {'text': text, 'location': location, 'notice': notice, 'truncated': bool(pointer)}


def main():
    # Hard CPU/address-space bounds where supported; the parent enforces wall time and Stop.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        if sys.platform.startswith('linux'):
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    except (ImportError, ValueError, OSError):
        pass
    logging.disable(logging.CRITICAL)
    request = json.load(sys.stdin)
    try:
        body = base64.b64decode(request['body'], validate=True)
        if request['format'] == 'pdf':
            result = pdf(body, request.get('pdf_page'))
        else:
            result = structured(body, request['json_pointer'], request['json_start'],
                                request['json_limit'], request['max_chars'])
        print(json.dumps({'result': result}, ensure_ascii=True))
    except ExtractionError as exc:
        print(json.dumps({'error': exc.status, 'message': str(exc)}))
    except Exception:
        print(json.dumps({'error': 'malformed_' + request['format'],
                          'message': 'Malformed or unsupported ' + request['format'].upper() + '; no evidence captured.'}))


if __name__ == '__main__':
    main()
