"""Report orchestration over reauthorized retained datasets; no workspace input or network."""
import hashlib
import json
import uuid

from app import api_dataset, dataset_analysis, dataset_report_html, sources
from app.api_dataset_formats import DatasetError, csv_text, render
from app.models import ToolPref
from app.web_extract_worker import decode_json

INSPECT = 'analyze_api_dataset'
REPORT = 'create_api_report'


def tools_allowed(ctx, names):
    for name in names:
        if ctx.allowed_tools is not None and name not in ctx.allowed_tools:
            raise DatasetError('Dataset analysis/reporting is outside the enabled tools for this turn.')
        pref = ctx.db.get(ToolPref, name, populate_existing=True)
        if pref and (not pref.enabled or pref.permission == 'deny'):
            raise DatasetError('Dataset analysis/reporting or export was disabled.')


def prepare(ctx, labels, sections, filters, turn_id):
    api_dataset.check_stop(ctx)
    pages = api_dataset.collect(ctx, labels, turn_id)
    files, coverage = api_dataset.assemble(pages)
    records = decode_json(files['records.json'].encode())
    identifier = pages[0]['adapter'].id_field
    if any(section.get('metric', 'count') == 'sum' and section.get('value_field') == identifier for section in sections):
        raise DatasetError('Record identifiers cannot be summed.')
    analysis = dataset_analysis.analyze(records, sections, filters, check=lambda: api_dataset.check_stop(ctx))
    return pages, files, coverage, analysis


def inspect(ctx, labels):
    turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
    with sources.LOCK:
        tools_allowed(ctx, [INSPECT])
        pages, _, coverage, analysis = prepare(ctx, labels, [], [], turn_id)
        result = {'coverage': coverage, **analysis}
        text = render(result)
        if len(text) > 12000:
            raise DatasetError('Column inspection is too large. Select a narrower dataset.')
        api_dataset.collect(ctx, labels, turn_id)
        api_dataset.check_stop(ctx)
        tools_allowed(ctx, [INSPECT])
        api_dataset.bind_pages(ctx, pages, turn_id)
    return text + '\nComputed from retained sources: ' + ' '.join(f'[{label}]' for label in labels) + '\n' + api_dataset.NOTICE


def report(ctx, labels, title, sections, filters=None):
    filters = filters or []
    turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
    with sources.LOCK:
        tools_allowed(ctx, [REPORT, api_dataset.NAME])
        pages, files, coverage, analysis = prepare(ctx, labels, sections, filters, turn_id)
        manifest = json.loads(files.pop('manifest.json'))
        manifest['analysis_recipe'] = {'title': title, 'sections': sections, 'filters': filters}
        manifest['analysis_method'] = analysis['method']
        manifest['selected_records'] = analysis['selected_records']
        files['analysis.json'] = render(analysis)
        flattened = [{**group, 'section': section['title'], 'group_by': section['group_by'],
                      'metric': section['metric'], 'value_field': section['value_field'],
                      'multi_valued_groups': section['multi_valued_groups']}
                     for section in analysis['sections'] for group in section['groups']]
        files['analysis.csv'] = csv_text(['section', 'group_by', 'metric', 'value_field', 'multi_valued_groups',
            'group', 'value', 'record_count', 'known_count', 'missing_count'], flattened)
        html = dataset_report_html.render(title, analysis, manifest)
        files = {'report.html': html, **files}
        manifest['files'] = {name: {'sha256': hashlib.sha256(text.encode()).hexdigest(), 'bytes': len(text.encode())}
                             for name, text in files.items()}
        manifest['verification'] = {'method': 'deterministic aggregation; shared chart/table values; staged file byte verification',
                                    'visual_review': 'not performed at generation time'}
        files['manifest.json'] = json.dumps(manifest, indent=2, ensure_ascii=False) + '\n'
        if sum(len(text.encode()) for text in files.values()) > api_dataset.MAX_BYTES:
            raise DatasetError('Report bundle exceeds 2 MiB. Select fewer sources, groups or sections.')
        api_dataset.collect(ctx, labels, turn_id)
        api_dataset.check_stop(ctx)
        tools_allowed(ctx, [REPORT, api_dataset.NAME])
        api_dataset.bind_pages(ctx, pages, turn_id)
        artifacts = api_dataset.publish(ctx, files, verify=True)
    return artifacts, {'coverage': coverage, 'selected_records': analysis['selected_records'],
                       'section_count': len(analysis['sections'])}
