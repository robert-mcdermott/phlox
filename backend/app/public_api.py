"""Fixed public read-query adapters; no model-supplied URLs, headers or POST bodies."""
from copy import deepcopy
import hashlib
import json
import threading
import time
from urllib.parse import urlencode

from app import public_api_adapters as adapters

from app import sources, web_fetch, web_formats
from app.models import Conversation, Source, SourceUse

NAME = 'query_public_api'
ENDPOINT = 'https://api.reporter.nih.gov/v2/projects/search'
PUBMED_ENDPOINT = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
PUBMED_SUMMARY_ENDPOINT = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
PUBMED_DETAIL_ENDPOINT = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
_PUBMED_NEXT_REQUEST = 0.0
PUBMED_INTERVAL = 0.4  # Below NCBI's three requests/second per-IP allowance without a key.
_PACE_LOCK = threading.Lock()
_NEXT_REQUEST = 0.0
MIN_INTERVAL = 1.0  # NIH requests no more than one request per second.
FIELDS = ['ApplId', 'SubprojectId', 'FiscalYear', 'ProjectNum', 'ProjectTitle', 'AwardAmount', 'Organization',
          'AgencyIcAdmin', 'AgencyIcFundings']
PARAMETERS = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'record_from': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$',
                        'description': 'Read article details from a saved API query/detail citation, with record_id. PubMed only currently.'},
        'record_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,64}$'},
        'section': {'type': 'string', 'enum': ['abstract', 'authors'], 'description': 'Record detail section, default abstract.'},
        'start': {'type': 'integer', 'minimum': 0, 'maximum': 500000, 'description': 'Detail offset: abstract character or matching author index; default 0.'},
        'max_chars': {'type': 'integer', 'minimum': 500, 'maximum': 4000, 'description': 'Abstract passage size; default 4000.'},
        'affiliation': {'type': 'string', 'minLength': 1, 'maxLength': 200,
                        'description': 'Authors section only: case-insensitive affiliation substring, e.g. Fred Hutch. Missing affiliations remain unknown.'},
        'adapter': {'type': 'string', 'enum': ['nih_projects', 'pubmed'], 'description': 'Defaults to nih_projects; choose pubmed for publication search.'},
        'query': {'type': 'string', 'minLength': 1, 'maxLength': 1000, 'pattern': r'^[^\x00-\x1f]+$',
                  'description': 'PubMed search expression, including field tags/date filters. Requires adapter=pubmed.'},
        'org_names': {'type': 'array', 'minItems': 1, 'maxItems': 5, 'uniqueItems': True,
                      'items': {'type': 'string', 'minLength': 2, 'maxLength': 200, 'pattern': r'^[^*?\x00-\x1f]+$'},
                      'description': 'Organization name fragments, e.g. Fred Hutch. Returned names must contain a fragment; this is not exact legal-entity matching.'},
        'fiscal_years': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'uniqueItems': True,
                         'items': {'type': 'integer', 'minimum': 1985, 'maximum': 2100}},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20,
                  'description': 'Records per API page, default NIH 5 or PubMed 2. Evidence must fit 6,000 characters.'},
        'continue_from': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$',
                          'description': 'Fetch the next API page using a retained citation from this query. Supply this instead of filters/limit; the saved recipe is reused.'},
    },
    'oneOf': [
        {'required': ['org_names', 'fiscal_years'], 'properties': {'adapter': {'const': 'nih_projects'}},
         'not': {'anyOf': [{'required': [key]} for key in ('continue_from', 'query', 'record_from', 'record_id', 'section', 'start', 'max_chars', 'affiliation')]}},
        {'required': ['adapter', 'query'], 'properties': {'adapter': {'const': 'pubmed'}},
         'not': {'anyOf': [{'required': [key]} for key in ('continue_from', 'org_names', 'fiscal_years', 'record_from', 'record_id', 'section', 'start', 'max_chars', 'affiliation')]}},
        {'required': ['continue_from'], 'not': {'anyOf': [
            {'required': [key]} for key in ('org_names', 'fiscal_years', 'query', 'limit', 'record_from', 'record_id', 'section', 'start', 'max_chars', 'affiliation')]}},
        {'required': ['record_from', 'record_id'], 'properties': {'section': {'const': 'abstract'}},
         'not': {'anyOf': [{'required': [key]} for key in ('org_names', 'fiscal_years', 'query', 'continue_from', 'limit', 'affiliation')]}},
        {'required': ['record_from', 'record_id', 'section'], 'properties': {'section': {'const': 'authors'}},
         'not': {'anyOf': [{'required': [key]} for key in ('org_names', 'fiscal_years', 'query', 'continue_from', 'max_chars')]}},
    ],
}


def recipe(arguments):
    if arguments.get('adapter') == 'pubmed':
        query = arguments['query'].strip()
        if not query or any(ord(char) < 32 for char in arguments['query']):
            raise web_fetch.FetchError('invalid_selection', 'Use a nonblank PubMed search expression without control characters.')
        return {'query': query, 'sort': 'pub date', 'offset': 0, 'limit': arguments.get('limit', 2)}
    names = [name.strip() for name in arguments['org_names']]
    if any(len(name) < 2 for name in names):
        raise web_fetch.FetchError('invalid_selection', 'Use nonblank organization name fragments of at least two characters.')
    return {'criteria': {'org_names': sorted(set(names)), 'fiscal_years': sorted(set(arguments['fiscal_years'])),
                         'exclude_subprojects': True, 'use_relevance': False},
            'include_fields': FIELDS.copy(), 'offset': 0, 'limit': arguments.get('limit', 5),
            'sort_field': 'appl_id', 'sort_order': 'asc'}


def continuation(ctx, label, turn_id, adapter_name=None):
    with sources.LOCK:
        conv = ctx.db.get(Conversation, ctx.conversation_id, populate_existing=True)
        if not conv or conv.user_id != ctx.user_id:
            raise web_fetch.FetchError('invalid_selection', 'API continuation unavailable.')
        row = ctx.db.query(Source).filter_by(conversation_id=conv.id, number=int(label[1:]), kind='web').populate_existing().first()
        adapter = adapters.ADAPTERS.get(row.location.get('adapter')) if row else None
        if (not row or not adapter or row.url != adapter.endpoint or row.location.get('format') != 'api'
                or (adapter_name is not None and adapter_name != adapter.name)
                or not sources.inspect_source(ctx.db, conv, row.id)['available']
                or (ctx.research and not ctx.db.get(SourceUse, (turn_id, row.id)))):
            raise web_fetch.FetchError('invalid_selection', 'API continuation unavailable in this conversation or research attempt; start a new query.')
        location = row.location
        if location.get('next_offset') is None:
            raise web_fetch.FetchError('invalid_selection', 'No next API page is available. Inspect the retained page for completion or window limits.')
        # Revalidate saved content and pagination before using a retained cursor.
        payload = json.dumps(location['request'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        if (hashlib.sha256(row.excerpt.encode()).hexdigest() != row.content_hash
                or hashlib.sha256(payload).hexdigest() != location['request_hash']):
            raise web_fetch.FetchError('invalid_selection', 'API continuation provenance is inconsistent.')
        try:
            page = adapter.validate_page(row.excerpt.encode(), location['request'])
        except (ValueError, KeyError, TypeError) as exc:
            raise web_fetch.FetchError('invalid_selection', 'API continuation page is inconsistent.') from exc
        if (page['next_offset'] != location['next_offset'] or page['ids'] != location['record_ids']
                or page['total'] != location['total_records']):
            raise web_fetch.FetchError('invalid_selection', 'API continuation metadata is inconsistent.')
        request = deepcopy(location['request'])
        request['offset'] = location['next_offset']
        previous = {'total': location['total_records'], 'ids': list(location['record_ids'])}
        if adapter.name == 'pubmed':
            previous['query_translation'] = page['query_translation']
        return adapter, request, previous


def pace(deadline, adapter_name='nih_projects'):
    global _NEXT_REQUEST, _PUBMED_NEXT_REQUEST
    # No queued thread holds the lock or reserves future slots while waiting. Stop and
    # the shared deadline remain responsive, including across parallel child calls.
    while True:
        deadline.check()
        with _PACE_LOCK:
            now = time.monotonic()
            wait = (_PUBMED_NEXT_REQUEST if adapter_name == 'pubmed' else _NEXT_REQUEST) - now
            if wait <= 0:
                if adapter_name == 'pubmed':
                    _PUBMED_NEXT_REQUEST = now + PUBMED_INTERVAL
                else:
                    _NEXT_REQUEST = now + MIN_INTERVAL
                return
        if deadline.cancel:
            deadline.cancel.wait(min(wait, 0.05))
        else:
            time.sleep(min(wait, 0.05))


def query(ctx, request, previous=None, adapter_name='nih_projects'):
    adapter = adapters.get(adapter_name)
    endpoint = adapter.endpoint
    policy = ctx.research.url_allowed if ctx.research else None
    if ctx.research and (ctx.research.state['options']['scope'] == 'documents' or not policy(endpoint) or (adapter_name == 'pubmed' and not policy(PUBMED_SUMMARY_ENDPOINT))):
        raise web_fetch.FetchError('scope_blocked', 'API is outside the selected research source scope.')
    payload = json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    with web_fetch.Deadline(ctx.cancel_event) as deadline:
        if adapter_name == 'pubmed':
            page, status = query_pubmed(request, previous, deadline, policy)
        else:
            pace(deadline)
            body, status = web_fetch.post_read_query(endpoint, payload, deadline, policy)
            page = web_formats.extract(body, adapter_name, deadline, request=request, previous=previous)
        deadline.check()
    # The API mints a new search_id on every request. Identity follows the retained
    # fields and pagination metadata, not that transient server token.
    return page, status, hashlib.sha256(page['text'].encode()).hexdigest(), hashlib.sha256(payload).hexdigest()


def query_pubmed(request, previous, deadline, policy):
    def read(endpoint, parameters):
        pace(deadline, 'pubmed')
        return web_fetch.read_api_query(endpoint + '?' + urlencode(
            {'db': 'pubmed', 'retmode': 'json', 'tool': 'phlox', **parameters}), deadline, policy)

    body, status = read(PUBMED_ENDPOINT, {'term': request['query'], 'sort': request['sort'],
        'retstart': request['offset'], 'retmax': min(request['limit'], 10000 - request['offset'])})
    search = web_formats.extract(body, 'pubmed_search', deadline, request=request, previous=previous)
    if search['ids']:
        body, status = read(PUBMED_SUMMARY_ENDPOINT, {'id': ','.join(search['ids'])})
    else:
        body = b'{"result":{"uids":[]}}'
    page = web_formats.extract(body, 'pubmed_summary', deadline, request=request, search=search)
    return page, status
