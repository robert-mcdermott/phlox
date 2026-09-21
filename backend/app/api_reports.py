"""Report orchestration over reauthorized retained datasets; no workspace input or network."""
import hashlib
import json
import uuid

from app import bulk_datasets, api_dataset, dataset_analysis, dataset_report_html, sources
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


def prepare(ctx, labels, sections, filters, turn_id, dataset_id=None):
    api_dataset.check_stop(ctx)
    pages = load_pages(ctx, labels, turn_id, dataset_id)
    files, coverage = api_dataset.assemble(pages, max_bytes=bulk_datasets.BUNDLE_BYTES if dataset_id else api_dataset.MAX_BYTES)
    records = decode_json(files['records.json'].encode())
    if dataset_id:
        manifest = json.loads(files['manifest.json'])
        manifest['dataset_id'] = dataset_id
        files['manifest.json'] = json.dumps(manifest, indent=2, ensure_ascii=False) + '\n'
    identifier = pages[0]['adapter'].id_field
    if any(section.get('metric', 'count') == 'sum' and section.get('value_field') == identifier for section in sections):
        raise DatasetError('Record identifiers cannot be summed.')
    analysis = dataset_analysis.analyze(records, sections, filters, check=lambda: api_dataset.check_stop(ctx))
    return pages, files, coverage, analysis


def load_pages(ctx, labels, turn_id, dataset_id):
    return bulk_datasets.load(ctx, dataset_id)[2] if dataset_id else api_dataset.collect(ctx, labels, turn_id)


def inspect(ctx, labels=None, dataset_id=None):
    turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
    with sources.LOCK:
        tools_allowed(ctx, [INSPECT])
        pages, _, coverage, analysis = prepare(ctx, labels, [], [], turn_id, dataset_id)
        result = {'coverage': coverage, **analysis}
        text = render(result)
        if len(text) > 12000:
            raise DatasetError('Column inspection is too large. Select a narrower dataset.')
        load_pages(ctx, labels, turn_id, dataset_id)
        api_dataset.check_stop(ctx)
        tools_allowed(ctx, [INSPECT])
        api_dataset.bind_pages(ctx, pages, turn_id)
    return text + '\nComputed from retained sources: ' + ' '.join(f'[{label}]' for label in dict.fromkeys(p['label'] for p in pages)) + '\n' + api_dataset.NOTICE


def compact_results(analysis):
    """Bounded exact values for model review; the complete analysis stays in analysis.json."""
    summaries = []
    remaining = 6000
    for section in analysis['sections']:
        summary = {key: section[key] for key in ('title', 'group_by', 'metric', 'value_field', 'multi_valued_groups')}
        summary.update(groups=[], total_groups=len(section['groups']))
        remaining -= len(json.dumps(summary))
        for group in section['groups'][:10]:
            # Number is a string subclass: preserve exact sums without float conversion.
            size = len(json.dumps(group))
            if size > remaining:
                break
            summary['groups'].append(group)
            remaining -= size
        summary['omitted_groups'] = len(section['groups']) - len(summary['groups'])
        summaries.append(summary)
    return summaries


def report(ctx, labels=None, title="Dataset report", sections=None, filters=None, dataset_id=None):
    filters = filters or []
    turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
    with sources.LOCK:
        tools_allowed(ctx, [REPORT, api_dataset.NAME])
        pages, files, coverage, analysis = prepare(ctx, labels, sections, filters, turn_id, dataset_id)
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
        if sum(len(text.encode()) for text in files.values()) > (bulk_datasets.BUNDLE_BYTES if dataset_id else api_dataset.MAX_BYTES):
            raise DatasetError('Report bundle exceeds its size allowance. Select fewer sources, groups or sections.')
        load_pages(ctx, labels, turn_id, dataset_id)
        api_dataset.check_stop(ctx)
        tools_allowed(ctx, [REPORT, api_dataset.NAME])
        api_dataset.bind_pages(ctx, pages, turn_id)
        artifacts = api_dataset.publish(ctx, files, verify=True)
    return artifacts, {'coverage': coverage, 'selected_records': analysis['selected_records'],
                       'section_count': len(analysis['sections']), 'computed_sections': compact_results(analysis),
                       'summary_notice': 'At most 10 groups per section; omitted_groups are not zero. Full results are in analysis.json. Numeric strings preserve exact values.',
                       'files': [a['path'] for a in artifacts],
                       'citations': list(dict.fromkeys(p['label'] for p in pages))}
