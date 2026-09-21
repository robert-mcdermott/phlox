"""Private checkpointed API pages, independent of the citation passage budget."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from types import SimpleNamespace
import uuid

from app import api_dataset, public_api, public_api_adapters, sources, web_fetch
from app.api_dataset_formats import DatasetError
from app.models import ApiDataset, Conversation, Source, ToolPref
from app.web_extract_worker import decode_json

MAX_BYTES = 16 * 1024 * 1024
BUNDLE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 50000
MAX_PAGES = 200
MAX_SECONDS = 600
# NIH's documented maximum is 500; keep other reviewed adapters conservative.
PAGE_SIZES = {'nih_projects': 500, 'pubmed': 100, 'clinical_trials': 100}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def authorize(ctx, dataset_id, *, check_cancel=True):
    if check_cancel:
        api_dataset.check_stop(ctx)
    dataset = ctx.db.get(ApiDataset, dataset_id, populate_existing=True)
    source = ctx.db.get(Source, dataset.source_id, populate_existing=True) if dataset else None
    conv = ctx.db.get(Conversation, ctx.conversation_id, populate_existing=True)
    if (not conv or conv.user_id != ctx.user_id or not source or source.conversation_id != conv.id
            or not sources.inspect_source(ctx.db, conv, source.id)['available']):
        raise DatasetError('Dataset missing, removed, expired or unavailable in this conversation.')
    if digest(source.excerpt) != source.content_hash:
        raise DatasetError('Dataset preview integrity check failed.')
    adapter = public_api_adapters.ADAPTERS.get((source.location or {}).get('adapter'))
    if not adapter or source.url != adapter.endpoint:
        raise DatasetError('Dataset API provenance is unavailable.')
    if ctx.research and (ctx.research.state['options']['scope'] == 'documents'
                        or not ctx.research.url_allowed(source.url)):
        raise DatasetError('Dataset is outside the current Research source scope.')
    return dataset, source, adapter


def load(ctx, dataset_id, *, manifest=True, check_cancel=True):
    dataset, source, adapter = authorize(ctx, dataset_id, check_cancel=check_cancel)
    stored = json.loads(dataset.pages)
    citation = ctx.db.get(Source, dataset.manifest_source_id, populate_existing=True) if manifest else source
    if manifest:
        if (not citation or citation.conversation_id != ctx.conversation_id or not citation.excerpt
                or digest(citation.excerpt) != citation.content_hash
                or (citation.location or {}).get('dataset_id') != dataset_id or sources.utc(citation.expires_at) <= datetime.now(timezone.utc)
                or (citation.location or {}).get('dataset_sha256') != digest(dataset.pages)):
            raise DatasetError('Dataset has unpublished progress. Resume collection to publish its current manifest.')
    pages = []
    previous = None
    end = 0
    for item in stored:
        if check_cancel:
            api_dataset.check_stop(ctx)
        text, request = item['text'], item['request']
        if item['hash'] != digest(text) or item['request_hash'] != digest(json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False)):
            raise DatasetError('Retained dataset integrity check failed.')
        validated = adapter.validate_page(text.encode(), request, previous, max_chars=1048576)
        if validated['offset'] != end:
            raise DatasetError('Retained dataset is not a contiguous prefix.')
        if pages and (pages[-1]['row'].location['next_offset'] != validated['offset']
                     or (adapter.name == 'clinical_trials' and pages[-1]['row'].location.get('next_page_token') != request.get('page_token'))):
            raise DatasetError('Retained dataset cursor chain is inconsistent.')
        row = SimpleNamespace(id=citation.id, url=adapter.endpoint, captured_at=datetime.fromisoformat(item['captured_at']),
            content_hash=item['hash'], location={**validated, 'request_hash': item['request_hash'], 'retrieval': item['retrieval']})
        pages.append({'row': row, 'label': f'S{citation.number}', 'request': request,
                      'data': decode_json(text.encode())['results'], 'adapter': adapter,
                      'query_translation': validated.get('query_translation'),
                      'offset': validated['offset'], 'end': validated['end'], 'total': validated['total']})
        previous = validated
        end = validated['end']
    return dataset, source, pages


def collection_access(ctx, names=('collect_api_dataset', public_api.NAME, api_dataset.NAME)):
    for name in names:
        pref = ctx.db.get(ToolPref, name, populate_existing=True)
        if (ctx.allowed_tools is not None and name not in ctx.allowed_tools) or (pref and (not pref.enabled or pref.permission == 'deny')):
            raise DatasetError('Collection, query or export is disabled for this turn.')


def quota(ctx, source, payload, *, new=False, dataset_id=None):
    rows = ctx.db.query(ApiDataset).join(Source, ApiDataset.source_id == Source.id).filter(
        Source.conversation_id == source.conversation_id).all()
    if (len(payload.encode()) > MAX_BYTES or (new and len(rows) >= 32)
            or sum(len(row.pages.encode()) for row in rows if row.id != dataset_id) + len(payload.encode()) > 64 * 1024 * 1024):
        raise DatasetError('Private dataset storage allowance reached (16 MiB/dataset, 32 datasets or 64 MiB/conversation).')


def start(ctx, label, turn_id):
    with sources.LOCK:
        collection_access(ctx)
        pages = api_dataset.collect(ctx, [label], turn_id)
        page = pages[0]
        if page['offset'] != 0:
            raise DatasetError('Start a bulk dataset with a preview at offset zero, using all requested years/filters together.')
        row = page['row']
        # Repeating the same start reuses its checkpoint instead of wasting requests/storage.
        existing = ctx.db.query(ApiDataset).filter_by(source_id=row.id).first()
        if existing:
            return existing.id
        payload = json.dumps([{'text': row.excerpt, 'request': page['request'], 'hash': row.content_hash,
            'request_hash': row.location['request_hash'], 'captured_at': sources.utc(row.captured_at).isoformat(),
            'retrieval': row.location.get('retrieval', [])}], ensure_ascii=False)
        quota(ctx, row, payload, new=True)
        dataset = ApiDataset(source_id=row.id, pages=payload)
        ctx.db.add(dataset)
        ctx.db.commit()
        return dataset.id


def run(ctx, source=None, dataset_id=None, max_pages=100, max_records=10000, max_seconds=300):
    turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
    if bool(source) == bool(dataset_id):
        raise DatasetError('Supply a preview source OR a dataset_id to resume.')
    dataset_id = dataset_id or start(ctx, source, turn_id)
    until = time.monotonic() + max_seconds
    if ctx.research:
        ctx.research.state['api_data_available'] = True
        until = min(until, time.monotonic() + max(0, ctx.research.limits['seconds'] -
                                                (time.time() - ctx.research.state['started_at'])))
        # A collection is one bounded source read; its internal pages are data, not passages.
        denied = ctx.research.admit_collection_page()
        if denied:
            raise DatasetError(denied + ' Dataset checkpoint: ' + dataset_id)
    attempts, reason, detail = 0, 'page_limit', ''

    def authorize_fetch():
        collection_access(ctx)
        authorize(ctx, dataset_id)
        if time.monotonic() >= until:
            raise web_fetch.FetchError('timeout', 'Collection acquisition time limit reached.')
        if ctx.research and (ctx.research.phase != 'gather' or ctx.research.exhausted(source_capacity=False)):
            raise web_fetch.FetchError('budget', 'Research acquisition budget ended.')

    while True:
        with sources.LOCK:
            collection_access(ctx)
            dataset, anchor, pages = load(ctx, dataset_id, manifest=False, check_cancel=False)
            last = pages[-1]
            adapter, previous = last['adapter'], last['row'].location
            next_offset = previous['next_offset']
            saved_payload = dataset.pages
            if last['end'] > max_records:
                raise DatasetError(f'Retained records exceed max_records. Resume dataset {dataset_id} with a larger explicit ceiling.')
        if ctx.progress:
            ctx.progress(f"Dataset {dataset_id}: saved {last['end']} of {last['total']} records.\n")
        if ctx.cancel_event and ctx.cancel_event.is_set():
            reason = 'stopped'
            break
        if next_offset is None:
            reason = 'api_window' if previous['window_exhausted'] else 'api_end'
            break
        if attempts >= max_pages:
            break
        if time.monotonic() >= until:
            reason = 'time_limit'
            break
        if last['end'] >= max_records:
            reason = 'record_limit'
            break
        request = deepcopy(last['request'])
        request.update(offset=next_offset, limit=min(PAGE_SIZES[adapter.name], max_records - last['end']))
        if adapter.name == 'clinical_trials':
            request['page_token'] = previous['next_page_token']
        try:
            authorize_fetch()
            attempts += 1
            page, _, content_hash, request_hash = public_api.query(ctx, request, previous, adapter.name,
                authorize=authorize_fetch, deadline_until=until, bulk=True)
            with sources.LOCK:
                authorize_fetch()
                current, anchor, _ = load(ctx, dataset_id, manifest=False)
                if current.pages != saved_payload:
                    raise DatasetError('Dataset advanced concurrently. Resume from the saved checkpoint.')
                items = json.loads(current.pages)
                if adapter.name == 'clinical_trials' and page.get('next_page_token') is not None and page['next_page_token'] in {
                        item['request'].get('page_token') for item in items} | {request.get('page_token')}:
                    raise DatasetError('API repeated a pagination cursor.')
                # Cross-page duplicates must fail even if not adjacent.
                ids = {str(record[adapter.id_field]) for p in pages for record in p['data']}
                if ids.intersection(map(str, page['ids'])):
                    raise DatasetError('API repeated records at different offsets.')
                items.append({'text': page['text'], 'request': request, 'hash': content_hash,
                    'request_hash': request_hash, 'captured_at': datetime.now(timezone.utc).isoformat(),
                    'retrieval': page['retrieval']})
                payload = json.dumps(items, ensure_ascii=False)
                quota(ctx, anchor, payload, dataset_id=dataset_id)
                current.pages = payload
                ctx.db.commit()  # Commit each validated page before the next network operation.
        except (DatasetError, web_fetch.FetchError) as exc:
            reason = 'stopped' if ctx.cancel_event and ctx.cancel_event.is_set() else 'acquisition_paused'
            detail = str(exc)
            break
    with sources.LOCK:
        dataset, anchor, pages = load(ctx, dataset_id, manifest=False, check_cancel=False)
        last = pages[-1]
        complete = last['end'] == last['total'] and last['row'].location['next_offset'] is None
        result = {'dataset_id': dataset_id, 'captured_records': last['end'], 'api_reported_matches': last['total'],
                  'complete': complete, 'more_pages': last['row'].location['next_offset'] is not None,
                  'stop_reason': reason, 'detail': detail, 'page_attempts': attempts,
                  'continue_with': {'dataset_id': dataset_id, 'max_records': max_records, 'max_pages': max_pages, 'max_seconds': max_seconds}}
        if reason == 'stopped':
            return json.dumps(result) + '\nStopped; validated pages saved. No files published.', [], True
        collection_access(ctx)
        # One immutable compact citation describes this dataset revision, regardless of page count.
        text = json.dumps({k: result[k] for k in ('dataset_id', 'captured_records', 'api_reported_matches', 'complete', 'more_pages')}, sort_keys=True)
        blocks = sources.capture_web(ctx.db, conversation_id=ctx.conversation_id, user_id=ctx.user_id, turn_id=turn_id,
            url=last['adapter'].endpoint, title=last['adapter'].title + ' — retained dataset', text=text,
            content_hash=digest(text), http_status=200, cancel=ctx.cancel_event,
            provenance={'format': 'api_dataset', 'adapter': last['adapter'].name, 'dataset_id': dataset_id,
                        'dataset_sha256': digest(dataset.pages), 'query': {k: v for k, v in pages[0]['request'].items()
                                                                         if k not in {'offset', 'limit', 'page_token'}}})
        if not blocks or not re.match(r'^\[S\d+\]', blocks[0]):
            return json.dumps(result) + '\nData saved; citation capacity unavailable. Resume in a new turn to publish files.', [], True
        label = re.match(r'^\[(S\d+)\]', blocks[0])[1]
        citation = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id, number=int(label[1:])).one()
        citation.expires_at = anchor.expires_at
        dataset.manifest_source_id = citation.id
        ctx.db.commit()
        result['citation'] = label
        if ctx.research:
            ctx.research.state['api_data_available'] = True
        try:
            artifacts, coverage = export(ctx, dataset_id, turn_id=turn_id)
            result['coverage'] = coverage
            result['files'] = [a['path'] for a in artifacts]
        except (DatasetError, OSError) as exc:
            result['export_error'] = str(exc)
            return json.dumps(result) + '\nData saved; file export failed. Resume or export dataset_id.', [], True
    return json.dumps(result) + f'\n[{label}] Dataset coverage manifest; full records and request hashes are in the files. ' + api_dataset.NOTICE, artifacts, False


def export(ctx, dataset_id, *, turn_id=None):
    turn_id = turn_id or (ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex)
    with sources.LOCK:
        collection_access(ctx, (api_dataset.NAME,))
        dataset, _, pages = load(ctx, dataset_id)
        files, coverage = api_dataset.assemble(pages, max_bytes=BUNDLE_BYTES)
        manifest = json.loads(files['manifest.json'])
        manifest['dataset'] = {'id': dataset_id, 'sha256': digest(dataset.pages), 'pages': len(pages),
                               'continue_with': {'dataset_id': dataset_id},
                               'next_page_available': pages[-1]['row'].location['next_offset'] is not None}
        files['manifest.json'] = json.dumps(manifest, indent=2, ensure_ascii=False) + '\n'
        if sum(len(text.encode()) for text in files.values()) > BUNDLE_BYTES:
            raise DatasetError('Dataset export exceeds the 32 MiB bundle allowance.')
        load(ctx, dataset_id)
        api_dataset.bind_pages(ctx, pages, turn_id)
        collection_access(ctx, (api_dataset.NAME,))
        return api_dataset.publish(ctx, files, verify=True), coverage
