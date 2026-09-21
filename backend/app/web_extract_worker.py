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


def nih_projects(body, request, previous=None, *, max_chars=6000):
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
    if len(text) > max_chars:
        raise ExtractionError('selection_empty', f'API page exceeds the {max_chars:,}-character page allowance. '
                              'Use a smaller limit for previews or a narrower bulk query; no partial records or continuation captured.')
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


def pubmed_records(body, request, previous=None, *, max_chars=6000):
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
    if len(text) > max_chars:
        raise ExtractionError('selection_empty', f'API page exceeds the {max_chars:,}-character page allowance. '
                              'Use a smaller limit for previews or a narrower bulk query; no partial records or continuation captured.')
    end = offset + len(records)
    return {'text': text, 'ids': ids, 'total': total, 'offset': offset, 'end': end,
            'next_offset': end if end < min(total, 10000) else None,
            'window_exhausted': end < total and end >= 10000, 'query_translation': translation}


def pubmed_summary(body, request, search, *, max_chars=6000):
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
    return pubmed_records(json.dumps({'meta': meta, 'results': records}).encode(), request, max_chars=max_chars)


def pubmed_detail(body, request):
    """Parse EFetch with no DTD/entity resolution in the isolated worker."""
    from xml.etree.ElementTree import TreeBuilder
    from xml.parsers import expat

    builder = TreeBuilder()
    parser = expat.ParserCreate()
    depth = nodes = 0

    def start(name, attrs):
        nonlocal depth, nodes
        depth += 1
        nodes += 1
        if depth > 64 or nodes > 50000:
            api_fail('PubMed XML exceeds structural limits.')
        builder.start(name, attrs)

    def end(name):
        nonlocal depth
        builder.end(name)
        depth -= 1

    def forbidden(*args):
        api_fail('PubMed XML entity declarations/references are not supported.')

    def doctype(name, system, public, internal):
        if name != 'PubmedArticleSet' or internal:
            forbidden()

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = parser.ExternalEntityRefHandler = forbidden
    try:
        parser.Parse(body, True)
        root = builder.close()
    except (expat.ExpatError, ValueError) as exc:
        if isinstance(exc, ExtractionError):
            raise
        api_fail('Malformed PubMed XML.')
    if root.tag != 'PubmedArticleSet' or len(root) != 1 or root[0].tag != 'PubmedArticle':
        api_fail('Expected exactly one PubMed journal article; missing, book or multi-record responses are unsupported.')
    citation = root.find('PubmedArticle/MedlineCitation')
    if citation is None or len(root[0].findall('MedlineCitation')) != 1:
        api_fail('Missing or duplicate PubMed citation.')

    def text(node):
        return ' '.join(''.join(node.itertext()).split()) if node is not None else ''

    identifier = text(citation.find('PMID'))
    article = citation.find('Article')
    if (identifier != request['record_id'] or article is None
            or len(citation.findall('PMID')) != 1 or len(citation.findall('Article')) != 1):
        api_fail('PubMed detail PMID does not match the selected record.')
    title = text(article.find('ArticleTitle'))
    if not title or len(title) > 1000:
        api_fail('Missing or oversized PubMed article title.')
    abstracts = []
    for abstract in [*article.findall('Abstract'), *citation.findall('OtherAbstract')]:
        sections = []
        for section in abstract.findall('AbstractText'):
            value = text(section)
            if value:
                heading = section.get('Label') or section.get('NlmCategory') or ''
                sections.append((heading + ': ' if heading else '') + value)
        if not sections and text(abstract) and abstract.find('AbstractText') is None:
            api_fail('Unsupported PubMed abstract structure.')
        if sections:
            language = abstract.get('Language', '')
            abstracts.append((f'Language: {language}\n' if language else '') + '\n\n'.join(sections))
    abstract_text = '\n\n'.join(abstracts)
    if len(abstract_text) > MAX_TEXT:
        api_fail('PubMed abstract exceeds the supported text limit.')
    authors = []
    author_list = article.find('AuthorList')
    for index, author in enumerate(article.findall('AuthorList/Author')):
        collective = text(author.find('CollectiveName'))
        personal = ' '.join(filter(None, [text(author.find('ForeName')) or text(author.find('Initials')),
                                          text(author.find('LastName')), text(author.find('Suffix'))]))
        if not (collective or personal):
            api_fail('PubMed author name is missing.')
        authors.append({'position': index + 1, 'name': collective or personal, 'collective': bool(collective),
                        'affiliations': [text(a) for a in [*author.findall('AffiliationInfo/Affiliation'), *author.findall('Affiliation')] if text(a)]})
    unassigned = [text(a) for a in [*article.findall('Affiliation'), *citation.findall('Affiliation')] if text(a)]
    normalized = {'record_id': identifier, 'title': title, 'abstract': abstract_text, 'authors': authors,
                  'author_list_complete': author_list.get('CompleteYN', 'unknown') if author_list is not None else 'unknown',
                  'unassigned_affiliations': unassigned}
    import hashlib
    version = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = {'record_id': identifier, 'title': title, 'url': 'https://pubmed.ncbi.nlm.nih.gov/' + identifier + '/',
              'section': request['section'], 'record_hash': version, 'full_text_retrieved': False}
    offset = request['start']
    if request['section'] == 'abstract':
        if offset > len(abstract_text) or (offset == len(abstract_text) and offset):
            api_fail('Abstract offset is outside the available text.')
        passage = abstract_text[offset:offset + request['max_chars']]
        end = offset + len(passage)
        result.update(abstract_status='available' if abstract_text else 'missing', text=passage,
                      selection={'start': offset, 'end': end, 'total': len(abstract_text),
                                 'next_start': end if end < len(abstract_text) else None, 'unit': 'characters'})
    else:
        fragment = request['affiliation'].casefold()
        selected = [a for a in authors if not fragment or any(fragment in affiliation.casefold() for affiliation in a['affiliations'])]
        if offset > len(selected) or (offset == len(selected) and offset):
            api_fail('Author offset is outside the matching author list.')
        end = min(len(selected), offset + request['limit'])
        result.update(authors=selected[offset:end], affiliation_filter=request['affiliation'],
                      returned_author_count=len(authors), authors_without_affiliations=sum(not a['affiliations'] for a in authors),
                      author_list_complete=normalized['author_list_complete'], unassigned_affiliations=unassigned,
                      selection={'start': offset, 'end': end, 'total': len(selected),
                                 'next_start': end if end < len(selected) else None, 'unit': 'authors'})
    rendered = ''.join(encode_json(result))
    if len(rendered) > 6000:
        raise ExtractionError('selection_empty', 'Article detail exceeds the evidence allowance. Use smaller max_chars for abstract or limit for authors; no partial evidence captured.')
    return {'text': rendered, 'record_hash': version, 'selection': result['selection']}


def trial_record(study):
    """Small explicit projection; absent registry fields stay unknown, never false/zero."""
    protocol = study['protocolSection']
    identifier = protocol['identificationModule']['nctId']
    status = protocol.get('statusModule', {})
    return {'nct_id': identifier, 'title': protocol['identificationModule']['briefTitle'],
            'overall_status': status.get('overallStatus'), 'has_results': study.get('hasResults'),
            'last_update_posted': status.get('lastUpdatePostDateStruct', {}).get('date'),
            'lead_sponsor': protocol.get('sponsorCollaboratorsModule', {}).get('leadSponsor', {}).get('name'),
            'phases': protocol.get('designModule', {}).get('phases'),
            'url': 'https://clinicaltrials.gov/study/' + identifier}


def trial_records(body, request, previous=None, *, max_chars=6000):
    import re
    value = decode_json(body)
    meta, records = value['meta'], value['results']
    total, offset, limit = (pubmed_integer(meta[k]) for k in ('total', 'offset', 'limit'))
    token = meta.get('next_page_token')
    if token is not None and (type(token) is not str or not token or len(token) > 2048
                               or any(ord(c) < 33 for c in token) or token == request.get('page_token')):
        api_fail('Invalid or repeated ClinicalTrials.gov page token.')
    if (not isinstance(records, list) or offset != request['offset'] or limit != request['limit']
            or offset > total or len(records) > min(limit, total - offset)):
        api_fail('ClinicalTrials.gov record count or pagination is inconsistent.')
    if previous and total != previous['total']:
        api_fail('ClinicalTrials.gov match count changed; restart the query.')
    ids = []
    for record in records:
        identifier = record.get('nct_id')
        if type(identifier) is not str or not re.fullmatch(r'NCT\d{8}', identifier):
            api_fail('Invalid ClinicalTrials.gov study ID.')
        if identifier in ids or (previous and identifier in previous['ids']):
            api_fail('Duplicate study or repeated adjacent page detected.')
        if type(record.get('title')) is not str or not record['title'].strip():
            api_fail('Missing study title.')
        for field in ('overall_status', 'last_update_posted', 'lead_sponsor'):
            if record.get(field) is not None and type(record[field]) is not str:
                api_fail('Invalid study metadata.')
        if record.get('has_results') is not None and type(record['has_results']) is not bool:
            api_fail('Invalid study results availability.')
        phases = record.get('phases')
        if phases is not None and (not isinstance(phases, list) or any(type(v) is not str for v in phases)):
            api_fail('Invalid study phases.')
        if request.get('statuses') and record.get('overall_status') not in request['statuses']:
            api_fail('Returned study does not match the requested recruitment status.')
        if record.get('url') != 'https://clinicaltrials.gov/study/' + identifier:
            api_fail('Study link does not match its ID.')
        ids.append(identifier)
    text = ''.join(encode_json(value))
    if len(text) > max_chars:
        api_fail(f'ClinicalTrials.gov page exceeds {max_chars:,} characters. Use a smaller preview or narrower bulk query.')
    end = offset + len(records)
    return {'text': text, 'ids': ids, 'total': total, 'offset': offset, 'end': end,
            'next_offset': end if token is not None else None, 'next_page_token': token, 'window_exhausted': False}


def trial_search(body, request, previous=None, *, max_chars=6000):
    value = decode_json(body)
    # The v2 API reports totalCount only on the first page, even with countTotal=true.
    # Carry that captured count forward; never invent a refreshed total. Empty pages
    # and terminal empty cursor pages are valid according to the official contract.
    studies = value['studies']
    if not isinstance(studies, list):
        api_fail('Expected ClinicalTrials.gov studies.')
    total = value.get('totalCount', previous['total'] if previous else None)
    normalized = {'meta': {'total': total, 'total_reported_on': 'first_page', 'offset': request['offset'],
                           'limit': request['limit'], 'next_page_token': value.get('nextPageToken')},
                  'results': [trial_record(study) for study in studies]}
    return trial_records(''.join(encode_json(normalized)).encode(), request, previous, max_chars=max_chars)


def trial_detail(body, request):
    import hashlib
    value = decode_json(body)
    record = trial_record(value)
    if record['nct_id'] != request['record_id']:
        api_fail('Study ID does not match the requested record.')
    # Validate the same metadata contract as search without claiming current status
    # must match an older search: study updates are retained as a new detail version.
    trial_records(''.join(encode_json({'meta': {'total': 1, 'offset': 0, 'limit': 1},
                                      'results': [record]})).encode(), {'offset': 0, 'limit': 1})
    protocol = value['protocolSection']
    section = request['section']
    sections = {
        'overview': {key: protocol.get(key) for key in ('identificationModule', 'statusModule',
            'sponsorCollaboratorsModule', 'conditionsModule', 'designModule', 'descriptionModule')},
        'eligibility': protocol.get('eligibilityModule'),
        'interventions': protocol.get('armsInterventionsModule'),
        'locations': protocol.get('contactsLocationsModule'),
        'results': value.get('resultsSection'),
    }
    selected = sections[section]
    if selected is not None and not isinstance(selected, dict):
        api_fail('Invalid study detail section.')
    text = ''.join(encode_json(selected)) if selected is not None else ''
    if len(text) > MAX_TEXT:
        api_fail('Study section exceeds 500,000 characters; no evidence captured.')
    start = request['start']
    if start > len(text) or (start and start == len(text)):
        api_fail('Study section character offset is out of range.')
    end = min(len(text), start + request['max_chars'])
    selection = {'start': start, 'end': end, 'total': len(text), 'next_start': end if end < len(text) else None,
                 'unit': 'characters'}
    digest = hashlib.sha256(''.join(encode_json(value)).encode()).hexdigest()
    result = {**record, 'record_id': record['nct_id'], 'section': section, 'record_hash': digest,
              'full_text_retrieved': False, 'section_status': 'missing' if selected is None else 'available',
              'text': text[start:end], 'selection': selection}
    encoded = ''.join(encode_json(result))
    if len(encoded) > 6000:
        api_fail('Study detail exceeds 6,000 serialized characters. Reduce max_chars.')
    return {'text': encoded, 'selection': selection, 'record_hash': digest}


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
            result = nih_projects(body, request['request'], request.get('previous'), max_chars=request.get('max_chars', 6000))
        elif request['format'] == 'pubmed_detail':
            result = pubmed_detail(body, request['request'])
        elif request['format'] == 'clinical_trials_search':
            result = trial_search(body, request['request'], request.get('previous'), max_chars=request.get('max_chars', 6000))
        elif request['format'] == 'clinical_trials_detail':
            result = trial_detail(body, request['request'])
        elif request['format'] == 'pubmed_search':
            result = pubmed_search(body, request['request'], request.get('previous'))
        elif request['format'] == 'pubmed_summary':
            result = pubmed_summary(body, request['request'], request['search'], max_chars=request.get('max_chars', 6000))
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
