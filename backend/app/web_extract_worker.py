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


def decode_json(body):
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
    return value


def structured(body, pointer, start, limit, max_chars):
    value = decode_json(body)
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


def nih_projects(body, request, previous=None):
    """Validate a bounded page before it becomes evidence or a continuation recipe."""
    def fail(message):
        raise ExtractionError('invalid_api_response', message + ' No evidence or continuation captured.')

    def integer(value):
        if not isinstance(value, Number) or not value.isascii() or not value.isdigit():
            fail('Expected an unsigned integer in API metadata or record identifiers.')
        return int(value)

    value = decode_json(body)
    if not isinstance(value, dict) or not isinstance(value.get('meta'), dict) or not isinstance(value.get('results'), list):
        fail('Expected RePORTER meta and results fields.')
    meta, records = value['meta'], value['results']
    total, offset, limit = (integer(meta.get(k)) for k in ('total', 'offset', 'limit'))
    if offset != request['offset'] or limit != request['limit'] or offset > total:
        fail('API pagination metadata does not match the requested page.')
    if len(records) != min(limit, total - offset):
        fail('API record count disagrees with its pagination metadata.')
    if previous is not None and total != previous['total']:
        fail('API total changed between pages; restart with a narrower query.')
    criteria, ids, selected = request['criteria'], [], []
    names = [name.casefold() for name in criteria['org_names']]
    fields = ('appl_id', 'subproject_id', 'fiscal_year', 'project_num', 'project_title', 'award_amount')
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('organization'), dict):
            fail('API project or organization has an unexpected type.')
        identifier, year = integer(record.get('appl_id')), integer(record.get('fiscal_year'))
        org = record['organization'].get('org_name')
        if type(org) is not str or not any(name in org.casefold() for name in names):
            fail('Returned organization does not match the requested name filters.')
        if year not in criteria['fiscal_years']:
            fail('Returned fiscal year does not match the requested filters.')
        if 'subproject_id' not in record or record['subproject_id'] is not None:
            fail('Expected a parent project with no subproject ID.')
        if identifier in ids or (previous and identifier in previous['ids']):
            fail('Duplicate project ID or repeated adjacent API page detected.')
        if (ids and identifier <= ids[-1]) or (previous and previous['ids'] and identifier <= previous['ids'][-1]):
            fail('API project identifiers are not in the requested ascending order; pagination cannot be trusted.')
        if any(type(record.get(k)) is not str or not record[k].strip() for k in ('project_num', 'project_title')):
            fail('Project number or title is missing or has an unexpected type.')
        if 'award_amount' not in record or (record['award_amount'] is not None
                                           and not isinstance(record['award_amount'], Number)):
            fail('Award amount is missing or is not a number/null.')
        ids.append(identifier)
        selected.append({**{key: record[key] for key in fields},
                         'organization': {key: record['organization'].get(key) for key in ('org_name', 'org_ipf_code', 'primary_uei')},
                         'agency_ic_admin': record.get('agency_ic_admin'),
                         'agency_ic_fundings': record.get('agency_ic_fundings')})
    text = ''.join(encode_json({'meta': {k: meta[k] for k in ('total', 'offset', 'limit')}, 'results': selected}))
    if len(text) > 6000:
        raise ExtractionError('selection_empty', 'API page exceeds the 6,000-character evidence allowance. '
                              'Start a query with a smaller limit; no partial records or continuation captured.')
    end = offset + len(records)
    return {'text': text, 'ids': ids, 'total': total, 'offset': offset, 'end': end,
            'next_offset': end if end < total and end <= 14999 else None,
            'window_exhausted': end < total and end > 14999}


def api_fail(message):
    raise ExtractionError('invalid_api_response', message + ' No evidence or continuation captured.')


def pubmed_integer(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 12:
        api_fail('Expected unsigned PubMed pagination metadata or PMID.')
    return int(value)


def pubmed_search(body, request, previous=None):
    value = decode_json(body)
    data = value.get('esearchresult') if isinstance(value, dict) else None
    if not isinstance(data, dict) or value.get('error') or data.get('ERROR') or data.get('errorlist'):
        api_fail('PubMed rejected the search or returned invalid search metadata.')
    # Unknown/ignored fields must not silently broaden a query. A no-results notice is
    # the one benign warning; retain it through an explicit empty page.
    warnings = data.get('warninglist', {})
    if not isinstance(warnings, dict) or any(v for k, v in warnings.items() if k != 'outputmessages'):
        api_fail('PubMed reported ignored terms or fields; revise the query.')
    messages = warnings.get('outputmessages', [])
    if not isinstance(messages, list) or any(m != 'No items found.' for m in messages):
        api_fail('PubMed reported a search warning; revise the query.')
    total, offset, limit = (pubmed_integer(data.get(k)) for k in ('count', 'retstart', 'retmax'))
    expected = min(request['limit'], max(0, min(total, 10000) - request['offset']))
    ids = data.get('idlist')
    if (offset != request['offset'] or offset > total or limit not in (request['limit'], expected)
            or not isinstance(ids, list) or len(ids) != expected):
        api_fail('PubMed pagination does not match the requested page.')
    if any(type(i) is not str or pubmed_integer(i) < 1 for i in ids) or len(set(ids)) != len(ids):
        api_fail('Invalid or duplicate PubMed IDs.')
    translation = data.get('querytranslation')
    if type(translation) is not str or (total and not translation.strip()):
        api_fail('Missing PubMed query translation.')
    if previous and (total != previous['total'] or translation != previous.get('query_translation')):
        api_fail('PubMed total or query translation changed between pages; restart the query.')
    if previous and set(ids) & set(previous['ids']):
        api_fail('Duplicate PMID or repeated adjacent API page detected.')
    return {'total': total, 'offset': offset, 'limit': request['limit'], 'ids': ids,
            'query_translation': translation}


def pubmed_records(body, request, previous=None):
    """Validate the normalized retained page as well as newly acquired records."""
    value = decode_json(body)
    if not isinstance(value, dict) or not isinstance(value.get('meta'), dict) or not isinstance(value.get('results'), list):
        api_fail('Expected PubMed page metadata and records.')
    meta, records = value['meta'], value['results']
    total, offset, limit = (pubmed_integer(meta.get(k)) for k in ('total', 'offset', 'limit'))
    translation = meta.get('query_translation')
    if type(translation) is not str or (total and not translation.strip()):
        api_fail('Missing PubMed query translation.')
    if (offset != request['offset'] or limit != request['limit'] or offset > total or offset >= 10000
            or len(records) != min(limit, min(total, 10000) - offset)):
        api_fail('PubMed record count or pagination is inconsistent.')
    if previous and (total != previous['total'] or translation != previous.get('query_translation')):
        api_fail('PubMed total or query translation changed between pages.')
    ids = []
    for record in records:
        if not isinstance(record, dict) or type(record.get('pmid')) is not str or pubmed_integer(record['pmid']) < 1:
            api_fail('Invalid PubMed record ID.')
        if record['pmid'] in ids or (previous and record['pmid'] in previous['ids']):
            api_fail('Duplicate PMID or repeated adjacent API page detected.')
        for field in ('title', 'journal', 'pubdate', 'volume', 'issue', 'pages', 'url'):
            if type(record.get(field)) is not str or (field == 'title' and not record[field].strip()):
                api_fail('Invalid PubMed bibliographic field.')
        if record['url'] != 'https://pubmed.ncbi.nlm.nih.gov/' + record['pmid'] + '/':
            api_fail('PubMed record URL does not match its PMID.')
        for field in ('authors', 'doi', 'pmc'):
            if not isinstance(record.get(field), list) or any(type(v) is not str for v in record[field]):
                api_fail('Invalid PubMed author or identifier list.')
        ids.append(record['pmid'])
    text = ''.join(encode_json(value))
    if len(text) > 6000:
        raise ExtractionError('selection_empty', 'API page exceeds the 6,000-character evidence allowance. '
                              'Start a query with a smaller limit; no partial records or continuation captured.')
    end = offset + len(records)
    return {'text': text, 'ids': ids, 'total': total, 'offset': offset, 'end': end,
            'next_offset': end if end < min(total, 10000) else None,
            'window_exhausted': end < total and end >= 10000, 'query_translation': translation}


def pubmed_summary(body, request, search):
    value = decode_json(body)
    data = value.get('result') if isinstance(value, dict) else None
    ids = search['ids']
    if (not isinstance(data, dict) or value.get('error') or data.get('uids') != ids
            or set(data) != {'uids', *ids}):
        api_fail('PubMed summaries do not match the searched PMIDs.')
    records = []
    for identifier in ids:
        record = data[identifier]
        if not isinstance(record, dict) or record.get('error') or record.get('uid') != identifier:
            api_fail('Missing or invalid PubMed summary.')
        authors, articleids = record.get('authors'), record.get('articleids')
        if (not isinstance(authors, list) or any(not isinstance(a, dict) or type(a.get('name')) is not str for a in authors)
                or not isinstance(articleids, list) or any(not isinstance(a, dict) or type(a.get('value')) is not str
                                                          or type(a.get('idtype')) is not str for a in articleids)):
            api_fail('Invalid PubMed author or identifier list.')
        records.append({'pmid': identifier, 'title': record.get('title'), 'journal': record.get('fulljournalname', ''),
                        'pubdate': record.get('pubdate'), 'volume': record.get('volume', ''),
                        'issue': record.get('issue', ''), 'pages': record.get('pages', ''),
                        'authors': [a['name'] for a in authors],
                        'doi': [a['value'] for a in articleids if a['idtype'] == 'doi'],
                        'pmc': [a['value'] for a in articleids if a['idtype'] == 'pmc'],
                        'url': 'https://pubmed.ncbi.nlm.nih.gov/' + identifier + '/'})
    meta = {k: search[k] for k in ('total', 'offset', 'limit', 'query_translation')}
    return pubmed_records(json.dumps({'meta': meta, 'results': records}).encode(), request)


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
        elif request['format'] == 'nih_projects':
            result = nih_projects(body, request['request'], request.get('previous'))
        elif request['format'] == 'pubmed_search':
            result = pubmed_search(body, request['request'], request.get('previous'))
        elif request['format'] == 'pubmed_summary':
            result = pubmed_summary(body, request['request'], request['search'])
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
