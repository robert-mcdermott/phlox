"""Adapter-specific CSV projections and calculations; never perform network/file IO."""
import csv
from decimal import Decimal, localcontext
from io import StringIO

from app.web_extract_worker import Number, encode_json


class DatasetError(ValueError):
    pass


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


def nih_files(ordered, coverage):
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
    return files


def pubmed_files(ordered, coverage):
    flattened = [{**r, **{k: '; '.join(r[k]) for k in ('authors', 'doi', 'pmc')}} for r in ordered]
    return {
        'records.json': render(ordered),
        'records.csv': csv_text(['pmid', 'title', 'authors', 'journal', 'pubdate', 'volume', 'issue',
                                'pages', 'doi', 'pmc', 'url'], flattened),
        'summary.csv': csv_text(['dataset_coverage', 'captured_unique_records', 'api_reported_matches'], [{
            'dataset_coverage': 'all_api_reported_matches' if coverage['all_reported_records_captured'] else 'partial',
            'captured_unique_records': coverage['captured_unique_records'],
            'api_reported_matches': coverage['api_reported_matches'],
        }]),
    }


def trial_files(ordered, coverage):
    flattened = [{**r, 'phases': '; '.join(r['phases']) if r['phases'] is not None else None} for r in ordered]
    return {
        'records.json': render(ordered),
        'records.csv': csv_text(['nct_id', 'title', 'overall_status', 'has_results', 'lead_sponsor',
                                'phases', 'last_update_posted', 'url'], flattened),
        'summary.csv': csv_text(['dataset_coverage', 'captured_unique_records', 'api_reported_matches'], [{
            'dataset_coverage': 'all_api_reported_matches' if coverage['all_reported_records_captured'] else 'partial',
            'captured_unique_records': coverage['captured_unique_records'],
            'api_reported_matches': coverage['api_reported_matches'],
        }]),
    }
