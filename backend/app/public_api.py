"""Fixed public read-query adapters; no model-supplied URLs, headers or POST bodies."""
from copy import deepcopy
import hashlib
import json
import threading
import time

from app import sources, web_fetch, web_formats
from app.models import Conversation, Source, SourceUse

NAME = 'query_public_api'
ENDPOINT = 'https://api.reporter.nih.gov/v2/projects/search'
_PACE_LOCK = threading.Lock()
_NEXT_REQUEST = 0.0
MIN_INTERVAL = 1.0  # NIH requests no more than one request per second.
FIELDS = ['ApplId', 'SubprojectId', 'FiscalYear', 'ProjectNum', 'ProjectTitle', 'AwardAmount', 'Organization',
          'AgencyIcAdmin', 'AgencyIcFundings']
PARAMETERS = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'adapter': {'type': 'string', 'enum': ['nih_projects'], 'description': 'NIH RePORTER parent-project search.'},
        'org_names': {'type': 'array', 'minItems': 1, 'maxItems': 5, 'uniqueItems': True,
                      'items': {'type': 'string', 'minLength': 2, 'maxLength': 200, 'pattern': r'^[^*?\x00-\x1f]+$'},
                      'description': 'Organization name fragments, e.g. Fred Hutch. Returned names must contain a fragment; this is not exact legal-entity matching.'},
        'fiscal_years': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'uniqueItems': True,
                         'items': {'type': 'integer', 'minimum': 1985, 'maximum': 2100}},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20,
                  'description': 'Records per API page, default 5. Start small; evidence must fit 6,000 characters.'},
        'continue_from': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$',
                          'description': 'Fetch the next API page using a retained citation from this query. Supply this instead of filters/limit; the saved recipe is reused.'},
    },
    'oneOf': [
        {'required': ['org_names', 'fiscal_years'], 'not': {'required': ['continue_from']}},
        {'required': ['continue_from'], 'not': {'anyOf': [
            {'required': [key]} for key in ('org_names', 'fiscal_years', 'limit')]}},
    ],
}


def recipe(arguments):
    names = [name.strip() for name in arguments['org_names']]
    if any(len(name) < 2 for name in names):
        raise web_fetch.FetchError('invalid_selection', 'Use nonblank organization name fragments of at least two characters.')
    return {'criteria': {'org_names': sorted(set(names)), 'fiscal_years': sorted(set(arguments['fiscal_years'])),
                         'exclude_subprojects': True, 'use_relevance': False},
            'include_fields': FIELDS.copy(), 'offset': 0, 'limit': arguments.get('limit', 5),
            'sort_field': 'appl_id', 'sort_order': 'asc'}


def continuation(ctx, label, turn_id):
    with sources.LOCK:
        conv = ctx.db.get(Conversation, ctx.conversation_id, populate_existing=True)
        if not conv or conv.user_id != ctx.user_id:
            raise web_fetch.FetchError('invalid_selection', 'API continuation unavailable.')
        row = ctx.db.query(Source).filter_by(conversation_id=conv.id, number=int(label[1:]), kind='web').populate_existing().first()
        if (not row or row.url != ENDPOINT or row.location.get('adapter') != 'nih_projects'
                or not sources.inspect_source(ctx.db, conv, row.id)['available']
                or (ctx.research and not ctx.db.get(SourceUse, (turn_id, row.id)))):
            raise web_fetch.FetchError('invalid_selection', 'API continuation unavailable in this conversation or research attempt; start a new query.')
        location = row.location
        if location.get('next_offset') is None:
            raise web_fetch.FetchError('invalid_selection', 'No next API page is available. Inspect the retained page for completion or window limits.')
        request = deepcopy(location['request'])
        request['offset'] = location['next_offset']
        previous = {'total': location['total_records'], 'ids': list(location['record_ids'])}
        return request, previous


def pace(deadline):
    global _NEXT_REQUEST
    # No queued thread holds the lock or reserves future slots while waiting. Stop and
    # the shared deadline remain responsive, including across parallel child calls.
    while True:
        deadline.check()
        with _PACE_LOCK:
            now = time.monotonic()
            wait = _NEXT_REQUEST - now
            if wait <= 0:
                _NEXT_REQUEST = now + MIN_INTERVAL
                return
        if deadline.cancel:
            deadline.cancel.wait(min(wait, 0.05))
        else:
            time.sleep(min(wait, 0.05))


def query(ctx, request, previous=None):
    policy = ctx.research.url_allowed if ctx.research else None
    if ctx.research and (ctx.research.state['options']['scope'] == 'documents' or not policy(ENDPOINT)):
        raise web_fetch.FetchError('scope_blocked', 'API is outside the selected research source scope.')
    payload = json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    with web_fetch.Deadline(ctx.cancel_event) as deadline:
        pace(deadline)
        body, status = web_fetch.post_read_query(ENDPOINT, payload, deadline, policy)
        page = web_formats.extract(body, 'nih_projects', deadline, request=request, previous=previous)
        deadline.check()
    # The API mints a new search_id on every request. Identity follows the retained
    # fields and pagination metadata, not that transient server token.
    return page, status, hashlib.sha256(page['text'].encode()).hexdigest(), hashlib.sha256(payload).hexdigest()
