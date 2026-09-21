"""Read selected API records through adapter-owned endpoints and retained evidence."""
import hashlib
import json

from app import public_api_adapters as adapters, public_api_transport, sources, web_fetch, web_formats
from app.models import Conversation, Source, SourceUse

NOTICE = ('This is selected article-detail evidence, not full article text. Abstracts report the authors\' claims, '
          'not independently verified findings. Affiliations describe the returned publication record, not current employment; '
          'missing affiliations are unknown. A search affiliation match does not identify every coauthor.')


def fail(message):
    raise web_fetch.FetchError('invalid_selection', message)


def load_source(ctx, label, turn_id):
    """Caller holds sources.LOCK. No administrator or cross-attempt read bypass."""
    conv = ctx.db.get(Conversation, ctx.conversation_id, populate_existing=True)
    row = (ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id, number=int(label[1:]))
           .populate_existing().first())
    if (not conv or conv.user_id != ctx.user_id or not row or row.kind != 'web'
            or not sources.inspect_source(ctx.db, conv, row.id)['available']):
        fail('API source is missing, removed, expired or unavailable in this conversation.')
    if ctx.research and (ctx.research.state['options']['scope'] == 'documents'
                         or not ctx.research.url_allowed(row.url) or not ctx.db.get(SourceUse, (turn_id, row.id))):
        fail('API source is outside this Research attempt or source scope.')
    payload = json.dumps(row.location.get('request'), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if (hashlib.sha256(row.excerpt.encode()).hexdigest() != row.content_hash
            or hashlib.sha256(payload).hexdigest() != row.location.get('request_hash')):
        fail('API source content or request provenance is inconsistent.')
    return row


def snapshot(row):
    adapter = adapters.ADAPTERS.get(row.location.get('adapter'))
    if (not adapter or not adapter.detail_endpoint or row.url != adapter.record_endpoint(row.location.get('record_id', ''))
            or row.location.get('format') != 'api_record'):
        fail('Source is not a supported API record-detail capture.')
    value = json.loads(row.excerpt)
    request = row.location['request']
    if (value.get('record_id') != row.location.get('record_id') or value.get('record_id') != request.get('record_id')
            or value.get('section') != request.get('section') or value.get('section') != row.location.get('section')
            or value.get('record_hash') != row.location.get('record_hash')
            or value.get('selection') != row.location.get('selection') or value.get('full_text_retrieved') is not False):
        fail('API record-detail provenance is inconsistent.')
    return adapter, value


def selected_record(ctx, label, identifier, turn_id):
    row = load_source(ctx, label, turn_id)
    adapter = adapters.ADAPTERS.get(row.location.get('adapter'))
    if not adapter or not adapter.detail_endpoint:
        fail('Record-detail reading is not implemented for this API adapter.')
    if row.location.get('format') == 'api_record':
        adapter, value = snapshot(row)
        if value['record_id'] != identifier:
            fail('Record ID does not belong to the selected detail source.')
        return adapter, row, value['record_hash']
    if row.location.get('format') != 'api' or row.url != adapter.endpoint:
        fail('Select a retained API query page or article-detail citation.')
    try:
        page = adapter.validate_page(row.excerpt.encode(), row.location['request'])
    except (ValueError, TypeError, KeyError) as exc:
        raise web_fetch.FetchError('invalid_selection', 'Saved API query page is inconsistent.') from exc
    if identifier not in {str(i) for i in page['ids']}:
        fail('Record ID does not belong to the selected query page.')
    return adapter, row, None


def read(ctx, arguments, turn_id):
    from app import public_api
    label, identifier = arguments['record_from'], arguments['record_id']
    with sources.LOCK:
        adapter, source, expected_version = selected_record(ctx, label, identifier, turn_id)
        source_id = source.id
    policy = ctx.research.url_allowed if ctx.research else None
    if policy and not policy(adapter.detail_endpoint):
        fail('Article details are outside the selected research domains.')
    if arguments.get('adapter', adapter.name) != adapter.name:
        fail('Adapter does not match the selected record source.')
    is_trial = adapter.name == 'clinical_trials'
    section = arguments.get('section', 'overview' if is_trial else 'abstract')
    allowed = public_api.TRIAL_SECTIONS if is_trial else ['abstract', 'authors']
    if section not in allowed or (is_trial and ('limit' in arguments or 'affiliation' in arguments)):
        fail('Section or selectors are not supported by this record adapter.')
    request = {'record_id': identifier, 'section': section, 'start': arguments.get('start', 0)}
    if section != 'authors':
        request['max_chars'] = arguments.get('max_chars', 3000 if is_trial else 4000)
    else:
        request.update(limit=arguments.get('limit', 5), affiliation=arguments.get('affiliation', '').strip())
    payload = json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    retrieval = []

    def authorize():
        public_api.authorize_read(ctx, turn_id, label)

    with web_fetch.Deadline(ctx.cancel_event) as deadline:
        # Adapter owns the wire request; neither arbitrary URLs nor raw bodies are accepted.
        url = adapter.record_url(identifier)
        body, status = public_api_transport.read(adapter.name, 'detail', url, deadline, policy,
            response_format=adapter.detail_response_format, authorize=authorize, retrieval=retrieval)
        result = web_formats.extract(body, adapter.detail_format, deadline, request=request)
        deadline.check()
    if expected_version and result['record_hash'] != expected_version:
        fail('Article/study details changed since the selected capture. Start again from the query page; do not combine versions.')
    with sources.LOCK:
        authorize()
        # Removal/expiry during the network request must not authorize new evidence.
        _, current, _ = selected_record(ctx, label, identifier, turn_id)
        if current.id != source_id:
            fail('Selected API source changed during retrieval.')
        location = {'format': 'api_record', 'adapter': adapter.name, 'method': 'GET',
                    'record_id': identifier, 'section': section, 'record_hash': result['record_hash'],
                    'request': request, 'request_hash': hashlib.sha256(payload).hexdigest(),
                    'selection': result['selection'], 'selected_from_source_id': source_id, 'retrieval': retrieval}
        captures = sources.capture_web(ctx.db, conversation_id=ctx.conversation_id, user_id=ctx.user_id,
            turn_id=turn_id, url=adapter.record_endpoint(identifier), title=f'{adapter.name} {identifier}: {section}',
            text=result['text'], content_hash=hashlib.sha256(result['text'].encode()).hexdigest(),
            http_status=status, cancel=ctx.cancel_event, provenance=location)
    if not captures or not captures[0].startswith('[S'):
        fail('Article detail could not be retained. No new evidence captured.')
    next_start = result['selection']['next_start']
    guidance = (f' More of this selection remains: use this detail citation as record_from with start={next_start}, '
                'the same record_id, section and affiliation filter.' if next_start is not None else
                ' End of this selected section/filter; this does not imply complete article content.')
    if is_trial:
        guidance = (f' More of this section remains: use this detail citation as record_from, the same record_id and section, '
                    f'and start={next_start}.' if next_start is not None else ' End of this selected section, not the entire study record.')
    return sources.INSTRUCTIONS + '\n' + (adapter.detail_notice or NOTICE) + guidance + '\n\n' + '\n\n'.join(captures)
