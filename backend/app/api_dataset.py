"""Deterministic exports of retained API pages; no network or model-authored records."""
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from app import public_api, sources
from app.models import Conversation, Source, SourceUse
from app.web_extract_worker import Number, decode_json, encode_json, nih_projects
from app.workspace.manager import workspace_dir

NAME = 'export_api_dataset'
MAX_BYTES = 2 * 1024 * 1024
NOTICE = ('These are retained parent-project records from one NIH RePORTER query, not verified NIH-only annual funding. '
          'Name fragments can match multiple organizations; agency scope and fiscal-year completeness require review. '
          'Known award sums exclude null amounts. Offset pagination is not a frozen database snapshot.')


class DatasetError(ValueError):
    pass


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
        if row.url != public_api.ENDPOINT or location.get('adapter') != 'nih_projects' or location.get('format') != 'api':
            raise DatasetError(f'{label} is not a supported NIH project-query page.')
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
        validated = nih_projects(row.excerpt.encode(), request)
        if (validated['offset'] != location['offset'] or validated['end'] != location['item_end']
                or validated['total'] != location['total_records']):
            raise DatasetError(f'{label} has inconsistent pagination provenance.')
        value = decode_json(row.excerpt.encode())
        pages.append({'row': row, 'label': label, 'request': request, 'data': value['results'],
                      'offset': validated['offset'], 'end': validated['end'], 'total': validated['total']})
    return pages


def render(value):
    return ''.join(encode_json(value)) + '\n'


def csv_text(columns, records):
    output = StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(columns)
    for record in records:
        cells = []
        for column in columns:
            value = record.get(column)
            cell = '' if value is None else str(value)
            # Preserve exact original strings in JSON; spreadsheet-facing text must not
            # become a formula. Numbers are generated/validated separately.
            if type(value) is str and (cell.lstrip().startswith(('=', '+', '-', '@')) or cell.startswith(('\t', '\r', '\n'))):
                cell = "'" + cell
            cells.append(cell)
        writer.writerow(cells)
    return output.getvalue()


def amount(value):
    if value is None:
        return None
    if not isinstance(value, Number):
        raise DatasetError('Award amount is not a captured JSON number.')
    parsed = Decimal(value)
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 100 or abs(parsed.as_tuple().exponent) > 100:
        raise DatasetError('Award amount exceeds the supported exact-decimal range.')
    return parsed


def assemble(pages):
    recipe = {k: v for k, v in pages[0]['request'].items() if k not in {'offset', 'limit'}}
    total = pages[0]['total']
    records, positions, provenance = {}, {}, []
    duplicates = 0
    for page in pages:
        if {k: v for k, v in page['request'].items() if k not in {'offset', 'limit'}} != recipe:
            raise DatasetError('Selected pages use different queries. Export each query separately.')
        if page['total'] != total:
            raise DatasetError('API totals changed across the selected pages; the dataset is inconsistent.')
        for position, record in enumerate(page['data'], page['offset']):
            key = str(record['appl_id'])
            if key in records and records[key] != record:
                raise DatasetError('Conflicting versions of the same project were selected. Choose one consistent capture.')
            if position in positions and positions[position] != key:
                raise DatasetError('Selected API pages disagree about record ordering.')
            if key in records and position not in positions:
                raise DatasetError('A project occurs at different offsets; API pagination changed.')
            duplicates += key in records
            records[key] = record
            positions[position] = key
        row = page['row']
        provenance.append({'label': page['label'], 'source_id': row.id, 'url': row.url,
                           'captured_at': sources.utc(row.captured_at).isoformat(),
                           'content_sha256': row.content_hash, 'request': page['request'],
                           'request_sha256': row.location['request_hash'], 'range': [page['offset'], page['end']]})
    ordered = [records[positions[p]] for p in sorted(positions)]
    if any(int(a['appl_id']) >= int(b['appl_id']) for a, b in zip(ordered, ordered[1:])):
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
    groups, flattened = {}, []
    with localcontext() as decimal_context:
        decimal_context.prec = 512  # Exact for bounded 100-digit/exponent inputs and <= 1,280 rows.
        for record in ordered:
            org = record['organization']
            # Keep known organization identifiers separate even when display names match.
            key = (org['org_name'], str(org.get('org_ipf_code') or ''), str(record['fiscal_year']))
            group = groups.setdefault(key, {'org_name': key[0], 'org_ipf_code': key[1],
                'fiscal_year': record['fiscal_year'], 'project_count': 0, 'known_amount_count': 0,
                'missing_amount_count': 0, 'known_award_amount_sum': Decimal(0)})
            value = amount(record['award_amount'])
            group['project_count'] += 1
            group['missing_amount_count' if value is None else 'known_amount_count'] += 1
            if value is not None:
                group['known_award_amount_sum'] += value
            flattened.append({**record, 'org_name': org['org_name'], 'org_ipf_code': org.get('org_ipf_code'),
                              'primary_uei': org.get('primary_uei')})
    summary = []
    for key in sorted(groups):
        group = groups[key]
        group['dataset_coverage'] = 'all_api_reported_matches' if coverage['all_reported_records_captured'] else 'partial'
        # An entirely unknown group must not look like zero funding.
        group['known_award_amount_sum'] = (Number(format(group['known_award_amount_sum'], 'f'))
                                           if group['known_amount_count'] else None)
        summary.append(group)
    files = {
        'records.json': render(ordered),
        'records.csv': csv_text(['appl_id', 'project_num', 'project_title', 'org_name', 'org_ipf_code',
                                'primary_uei', 'fiscal_year', 'award_amount'], flattened),
        'summary.csv': csv_text(['dataset_coverage', 'org_name', 'org_ipf_code', 'fiscal_year', 'project_count', 'known_amount_count',
                                'missing_amount_count', 'known_award_amount_sum'], summary),
    }
    manifest = {'version': 1, 'adapter': 'nih_projects', 'created_at': datetime.now(timezone.utc).isoformat(),
                'notice': NOTICE, 'query': recipe, 'coverage': coverage, 'sources': provenance,
                'csv_text_policy': 'Formula-like text cells are prefixed with an apostrophe; records.json preserves original strings. Nulls are blank in CSV.',
                'files': {name: {'sha256': hashlib.sha256(text.encode()).hexdigest(), 'bytes': len(text.encode())}
                          for name, text in files.items()}}
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


def export(ctx, labels):
    turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
    with sources.LOCK:
        check_stop(ctx)
        pages = collect(ctx, labels, turn_id)
        files, coverage = assemble(pages)
        check_stop(ctx)
        # Recheck expiry immediately before publication. Lock also excludes deletion.
        collect(ctx, labels, turn_id)
        missing = [p['row'] for p in pages if not ctx.db.get(SourceUse, (turn_id, p['row'].id))]
        if ctx.db.query(SourceUse).filter_by(turn_id=turn_id).count() + len(missing) > sources.MAX_TURN_SOURCES:
            raise DatasetError('Not enough source capacity to bind the dataset citations to this turn.')
        for row in missing:
            ctx.db.add(SourceUse(turn_id=turn_id, source_id=row.id, query=''))
        ctx.db.commit()
        artifacts = publish(ctx, files)
    return artifacts, coverage
