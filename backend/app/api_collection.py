"""Bounded acquisition from a verified preview; retained citations are the checkpoints."""
from datetime import datetime, timezone
import json
import re
import time
from types import SimpleNamespace
import uuid

from app import api_dataset, public_api, sources, web_fetch
from app.models import ToolPref
from app.web_extract_worker import decode_json

NAME = 'collect_api_dataset'
MAX_PAGES = 20
MAX_RECORDS = 1000
MAX_SECONDS = 120


def access(ctx, labels, turn_id, *, check_cancel=True):
    # Approval for this tool covers its described read/write operation. A disabled or
    # denied constituent capability must not become accessible via collection.
    for name in (NAME, public_api.NAME, api_dataset.NAME):
        if ctx.allowed_tools is not None and name not in ctx.allowed_tools:
            raise api_dataset.DatasetError('API collection is outside the enabled tools for this turn.')
        pref = ctx.db.get(ToolPref, name, populate_existing=True)
        if pref and (not pref.enabled or pref.permission == 'deny'):
            raise api_dataset.DatasetError('API collection, querying or export was disabled. No further work performed.')
    with sources.LOCK:
        pages = api_dataset.collect(ctx, labels, turn_id, check_cancel=check_cancel)
        # Require a single contiguous prefix in the supplied order. No silently skipped
        # gaps, reordered pages, guessed cursors or overlapping query segments.
        end = 0
        for i, page in enumerate(pages):
            if page['offset'] != end:
                raise api_dataset.DatasetError('Collection requires ordered contiguous pages starting at offset zero.')
            if i:
                prior = pages[i - 1]
                if prior['row'].location.get('next_offset') != page['offset']:
                    raise api_dataset.DatasetError('Saved pages do not form a continuation chain.')
                if page['adapter'].name == 'clinical_trials' and (
                    prior['row'].location.get('next_page_token') != page['request'].get('page_token')):
                    raise api_dataset.DatasetError('Saved study pages do not follow the retained cursor chain.')
            end = page['end']
        _, coverage = api_dataset.assemble(pages)
        return pages, coverage


def run(ctx, labels, max_pages=5, max_records=200, max_seconds=60):
    labels = list(labels)
    turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
    until = time.monotonic() + max_seconds
    if ctx.research:
        until = min(until, time.monotonic() + max(0, ctx.research.limits['seconds'] -
                                                 (time.time() - ctx.research.state['started_at'])))
    with sources.LOCK:
        pages, coverage = access(ctx, labels, turn_id)
        api_dataset.bind_pages(ctx, pages, turn_id)
    if coverage['captured_unique_records'] > max_records:
        raise api_dataset.DatasetError('Retained pages already exceed max_records. Increase it within the tool ceiling or select fewer pages.')
    initial_count = len(labels)
    attempts = 0
    reason = 'page_limit'
    detail = ''

    def progress():
        if ctx.progress:
            ctx.progress(f"Saved {len(labels)} pages / {coverage['captured_unique_records']} records. "
                         'Continuation labels: ' + json.dumps(labels) + '\n')

    def authorize():
        if time.monotonic() >= until:
            raise web_fetch.FetchError('timeout', 'Collection acquisition time limit reached.')
        access(ctx, labels, turn_id)
        if ctx.research and (ctx.research.phase != 'gather' or ctx.research.exhausted(source_capacity=False)):
            raise web_fetch.FetchError('budget', 'Research gathering budget or stage ended.')

    progress()
    while True:
        last = pages[-1]
        location = last['row'].location
        # A reported total is not terminal if the service supplied a next cursor.
        if location.get('next_offset') is None:
            reason = 'api_window' if location.get('window_exhausted') else 'api_end'
            break
        if ctx.cancel_event and ctx.cancel_event.is_set():
            reason = 'stopped'
            break
        if len(labels) >= 64:
            reason = 'source_limit'
            break
        if attempts >= max_pages:
            break
        if time.monotonic() >= until:
            reason = 'time_limit'
            break
        if coverage['captured_unique_records'] + last['request']['limit'] > max_records:
            reason = 'record_limit'
            break
        if sources.remaining_capacity(ctx.db, ctx.conversation_id, turn_id) < 1:
            reason = 'source_limit'
            break
        try:
            authorize()
            adapter, request, previous = public_api.continuation(ctx, labels[-1], turn_id)
            if ctx.research:
                ctx.research.state['source_capacity'] = sources.remaining_capacity(ctx.db, ctx.conversation_id, turn_id)
                denied = ctx.research.admit_collection_page()
                if denied:
                    reason, detail = 'research_limit', denied
                    break
            attempts += 1

            def validate(page, provenance, digest):
                # Validate cross-page ordering, duplicates, versions and bundle size
                # BEFORE saving a new page. Real source IDs replace these placeholders.
                row = SimpleNamespace(id='pending', url=adapter.endpoint, captured_at=datetime.now(timezone.utc),
                                      content_hash=digest, location=provenance)
                candidate = {'row': row, 'label': 'pending', 'request': request,
                             'data': decode_json(page['text'].encode())['results'], 'adapter': adapter,
                             'query_translation': page.get('query_translation'),
                             'offset': page['offset'], 'end': page['end'], 'total': page['total']}
                files, _ = api_dataset.assemble([*pages, candidate])
                # Leave room for final source IDs and the compact collection manifest.
                if sum(len(text.encode()) for text in files.values()) > api_dataset.MAX_BYTES - 16384:
                    raise api_dataset.DatasetError('Collection bundle size allowance reached.')
                if adapter.name == 'clinical_trials' and page.get('next_page_token') is not None:
                    seen_tokens = {p['request'].get('page_token') for p in pages}
                    seen_tokens.add(request.get('page_token'))
                    if page['next_page_token'] in seen_tokens:
                        raise api_dataset.DatasetError('API repeated a pagination cursor; collection stopped.')

            _, captures = public_api.capture_query(ctx, adapter, request, previous, turn_id,
                label=labels[-1], deadline_until=until, authorize_extra=authorize, validate=validate)
            # Labels come only from the internal source capture, never an API body.
            new_labels = [re.match(r'^\[(S\d+)\]', block).group(1) for block in captures]
            labels.extend(new_labels)
            pages, coverage = access(ctx, labels, turn_id)
            progress()
        except web_fetch.FetchError as exc:
            reason = 'stopped' if ctx.cancel_event and ctx.cancel_event.is_set() else (
                'time_limit' if time.monotonic() >= until else 'api_failure')
            detail = str(exc)
            break
        except api_dataset.DatasetError as exc:
            reason, detail = 'validation_or_storage_limit', str(exc)
            break
    # Reauthorize the whole prefix, even when a later request failed. Never publish
    # removed/expired evidence or fall back to stale in-memory pages.
    stopped = bool(ctx.cancel_event and ctx.cancel_event.is_set())
    pages, coverage = access(ctx, labels, turn_id, check_cancel=False)
    if stopped:
        reason = 'stopped'
    state = {'stop_reason': reason, 'detail': detail, 'labels': labels,
             'pages_added': len(labels) - initial_count, 'page_attempts': attempts,
             'next_page_available': pages[-1]['row'].location.get('next_offset') is not None,
             'limits': {'max_pages': max_pages, 'max_records': max_records, 'max_seconds': max_seconds}}
    state['complete'] = not stopped and coverage['all_reported_records_captured'] and not state['next_page_available']
    if state['next_page_available']:
        state['continue_with'] = {'tool': NAME, 'labels': labels, 'max_pages': max_pages,
                                  'max_records': max_records, 'max_seconds': max_seconds}
        if reason == 'record_limit':
            state['continuation_note'] = 'Continuation needs a larger explicitly chosen max_records (up to 1000), or a narrower new query.'
        elif reason in {'source_limit', 'api_window'}:
            state['continuation_note'] = 'A source/API window ceiling may require a narrower new query; repeating this call does not raise it.'
    artifacts = []
    if not stopped:
        try:
            artifacts, coverage = api_dataset.export(ctx, labels, collection=state, turn_id=turn_id)
        except (api_dataset.DatasetError, OSError):
            # Preserve an actionable checkpoint after file failure, but never reuse
            # stale counts/labels if the failure was actually source revocation.
            pages, coverage = access(ctx, labels, turn_id, check_cancel=False)
            if ctx.cancel_event and ctx.cancel_event.is_set():
                stopped = True
                state['stop_reason'] = 'stopped'
            else:
                state['export_error'] = 'Files could not be published. Saved pages remain available; explicitly export or continue these labels.'
            state['complete'] = False
    state['coverage'] = coverage
    state['files_created'] = bool(artifacts)
    content = ('Collection ' + ('stopped; saved pages retained' if stopped else
               'saved; file export failed' if state.get('export_error') else
               'finished; all API-reported records captured' if coverage['all_reported_records_captured'] and not state['next_page_available'] else
               'partial; more work or a narrower query is needed') + '.\n' + json.dumps(state, ensure_ascii=False) +
               '\nRetained pages: ' + ' '.join(f'[{label}]' for label in labels) +
               '\nRecords are in the saved sources/files, not this response. Use read_web_source before making record-level claims. ' +
               api_dataset.NOTICE)
    return content, artifacts, stopped or bool(state.get('export_error'))
