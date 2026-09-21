"""Deterministic exports of retained API pages; no network or model-authored records."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from app import public_api_adapters as adapters, public_api_details, sources
from app.api_dataset_formats import DatasetError
from app.models import Conversation, Source, SourceUse
from app.web_extract_worker import decode_json
from app.workspace.manager import workspace_dir

NAME = 'export_api_dataset'
MAX_BYTES = 2 * 1024 * 1024
NOTICE = 'Coverage describes the selected retained records, not independent verification of upstream completeness.'


def check_stop(ctx):
    if ctx.cancel_event and ctx.cancel_event.is_set():
        raise DatasetError('Dataset export stopped. No files published.')


def collect(ctx, labels, turn_id):
    """Caller holds sources.LOCK through final publication so revocation cannot race it."""
    conv = ctx.db.get(Conversation, ctx.conversation_id, populate_existing=True)
    if not conv or conv.user_id != ctx.user_id:
        raise DatasetError('Dataset sources unavailable in this conversation.')
    pages = []
    for label in labels:
        check_stop(ctx)
        row = ctx.db.query(Source).filter_by(conversation_id=conv.id, number=int(label[1:])).populate_existing().first()
        if not row or row.kind != 'web' or not sources.inspect_source(ctx.db, conv, row.id)['available']:
            raise DatasetError(f'{label} is missing, removed, expired or unavailable. No sources were silently skipped.')
        location = row.location
        adapter = adapters.ADAPTERS.get(location.get('adapter'))
        if not adapter or row.url != adapter.endpoint or location.get('format') != 'api':
            raise DatasetError(f'{label} is not a supported API-query page.')
        if ctx.research and (ctx.research.state['options']['scope'] == 'documents'
                             or not ctx.research.url_allowed(row.url)
                             or not ctx.db.get(SourceUse, (turn_id, row.id))):
            raise DatasetError(f'{label} is outside this Research attempt or source scope.')
        if hashlib.sha256(row.excerpt.encode()).hexdigest() != row.content_hash:
            raise DatasetError(f'{label} has inconsistent retained content.')
        request = location['request']
        payload = json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        if hashlib.sha256(payload).hexdigest() != location['request_hash']:
            raise DatasetError(f'{label} has inconsistent request provenance.')
        # Revalidate persisted values, not model-provided data or just the HTTP status.
        validated = adapter.validate_page(row.excerpt.encode(), request)
        if (validated['offset'] != location['offset'] or validated['end'] != location['item_end']
                or validated['total'] != location['total_records']):
            raise DatasetError(f'{label} has inconsistent pagination provenance.')
        value = decode_json(row.excerpt.encode())
        pages.append({'row': row, 'label': label, 'request': request, 'data': value['results'], 'adapter': adapter,
                      'query_translation': validated.get('query_translation'),
                      'offset': validated['offset'], 'end': validated['end'], 'total': validated['total']})
    return pages


def assemble(pages, details=None):
    adapter = pages[0]['adapter']
    recipe = {k: v for k, v in pages[0]['request'].items() if k not in {'offset', 'limit'}}
    total = pages[0]['total']
    records, positions, provenance = {}, {}, []
    duplicates = 0
    for page in pages:
        if (page['adapter'] != adapter or page['query_translation'] != pages[0]['query_translation']
                or {k: v for k, v in page['request'].items() if k not in {'offset', 'limit'}} != recipe):
            raise DatasetError('Selected pages use different queries. Export each query separately.')
        if page['total'] != total:
            raise DatasetError('API totals changed across the selected pages; the dataset is inconsistent.')
        for position, record in enumerate(page['data'], page['offset']):
            key = str(record[adapter.id_field])
            if key in records and records[key] != record:
                raise DatasetError('Conflicting versions of the same record were selected. Choose one consistent capture.')
            if position in positions and positions[position] != key:
                raise DatasetError('Selected API pages disagree about record ordering.')
            if key in records and position not in positions:
                raise DatasetError('A record occurs at different offsets; API pagination changed.')
            duplicates += key in records
            records[key] = record
            positions[position] = key
        row = page['row']
        provenance.append({'label': page['label'], 'source_id': row.id, 'url': row.url,
                           'captured_at': sources.utc(row.captured_at).isoformat(),
                           'content_sha256': row.content_hash, 'request': page['request'],
                           'request_sha256': row.location['request_hash'], 'range': [page['offset'], page['end']]})
    ordered = [records[positions[p]] for p in sorted(positions)]
    if adapter.ascending_ids and any(int(a[adapter.id_field]) >= int(b[adapter.id_field]) for a, b in zip(ordered, ordered[1:])):
        raise DatasetError('Selected records are not in ascending API order.')
    gaps, end = [], 0
    for page in sorted(pages, key=lambda p: p['offset']):
        if page['offset'] > end:
            gaps.append([end, page['offset']])
        end = max(end, page['end'])
    if end < total:
        gaps.append([end, total])
    coverage = {'captured_unique_records': len(ordered), 'api_reported_matches': total,
                'all_reported_records_captured': not gaps and len(ordered) == total,
                'missing_record_ranges': gaps, 'duplicate_records_removed': duplicates}
    files = adapter.dataset_files(ordered, coverage)
    if details:
        files['record_details.json'] = json.dumps([d['data'] for d in details], indent=2, ensure_ascii=False) + '\n'
    manifest = {'version': 1, 'adapter': adapter.name, 'created_at': datetime.now(timezone.utc).isoformat(),
                'notice': adapter.notice, 'query': recipe, 'coverage': coverage, 'sources': provenance,
                'csv_text_policy': 'Formula-like text cells are prefixed with an apostrophe; records.json preserves original strings. Nulls are blank in CSV.',
                'files': {name: {'sha256': hashlib.sha256(text.encode()).hexdigest(), 'bytes': len(text.encode())}
                          for name, text in files.items()}}
    if details:
        manifest['record_detail_sources'] = [d['provenance'] for d in details]
        manifest['record_detail_notice'] = public_api_details.NOTICE + ' Selections may be partial or affiliation-filtered; they do not change query coverage.'
    if pages[0]['query_translation'] is not None:
        manifest['query_translation'] = pages[0]['query_translation']
    files['manifest.json'] = json.dumps(manifest, indent=2, ensure_ascii=False) + '\n'
    if sum(len(text.encode()) for text in files.values()) > MAX_BYTES:
        raise DatasetError('Dataset export exceeds the 2 MiB bundle allowance. Select fewer pages.')
    return files, coverage


def publish(ctx, files):
    root = workspace_dir(ctx.conversation_id).resolve()
    staging = Path(tempfile.mkdtemp(prefix='.api-export-', dir=root))
    destination = root / ('api-dataset-' + uuid.uuid4().hex[:12])
    try:
        for name, text in files.items():
            check_stop(ctx)
            (staging / name).write_text(text, encoding='utf-8', newline='')
        check_stop(ctx)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return [{'path': str((destination / name).relative_to(root)), 'name': name,
             'ext': Path(name).suffix, 'size': len(text.encode())} for name, text in files.items()]


def collect_details(ctx, labels, turn_id, pages):
    if not labels:
        return []
    if any(page['adapter'] != pages[0]['adapter'] for page in pages):
        raise DatasetError('Selected pages use different queries. Export each query separately.')
    details, versions = [], {}
    adapter = pages[0]['adapter']
    identifiers = {str(record[adapter.id_field]) for page in pages for record in page['data']}
    for label in labels:
        check_stop(ctx)
        row = public_api_details.load_source(ctx, label, turn_id)
        detail_adapter, value = public_api_details.snapshot(row)
        identifier = value['record_id']
        if detail_adapter != adapter or identifier not in identifiers:
            raise DatasetError('Record details must belong to records in the selected query pages.')
        if identifier in versions and versions[identifier] != value['record_hash']:
            raise DatasetError('Selected article details contain conflicting record versions. Export one version at a time.')
        versions[identifier] = value['record_hash']
        details.append({'row': row, 'data': value, 'provenance': {
            'label': label, 'source_id': row.id, 'url': row.url, 'record_id': identifier,
            'captured_at': sources.utc(row.captured_at).isoformat(), 'content_sha256': row.content_hash,
            'request': row.location['request'], 'request_sha256': row.location['request_hash'],
            'record_sha256': value['record_hash'], 'selection': value['selection'],
            'selected_from_source_id': row.location['selected_from_source_id'],
        }})
    return details


def export(ctx, labels, detail_labels=None):
    detail_labels = detail_labels or []
    if len(labels) + len(detail_labels) > 64 or set(labels) & set(detail_labels):
        raise DatasetError('Supply at most 64 distinct query/detail source labels in total.')
    turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
    with sources.LOCK:
        check_stop(ctx)
        pages = collect(ctx, labels, turn_id)
        details = collect_details(ctx, detail_labels, turn_id, pages)
        files, coverage = assemble(pages, details)
        check_stop(ctx)
        # Recheck expiry immediately before publication. Lock also excludes deletion.
        collect(ctx, labels, turn_id)
        collect_details(ctx, detail_labels, turn_id, pages)
        missing = [p['row'] for p in [*pages, *details] if not ctx.db.get(SourceUse, (turn_id, p['row'].id))]
        if ctx.db.query(SourceUse).filter_by(turn_id=turn_id).count() + len(missing) > sources.MAX_TURN_SOURCES:
            raise DatasetError('Not enough source capacity to bind the dataset citations to this turn.')
        for row in missing:
            ctx.db.add(SourceUse(turn_id=turn_id, source_id=row.id, query=''))
        ctx.db.commit()
        artifacts = publish(ctx, files)
    return artifacts, coverage
