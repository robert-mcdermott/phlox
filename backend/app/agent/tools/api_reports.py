"""Bounded data inspection and deterministic reports, with ordinary read/write permissions."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator

from app import api_reports
from app.api_dataset_formats import DatasetError
from app.agent.tools.base import Tool, ToolResult
from app.web_extract_worker import ExtractionError

FIELDS = {
    'labels': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'uniqueItems': True,
               'items': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$'}},
    'sections': {'type': 'array', 'maxItems': 6, 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {
            'title': {'type': 'string', 'minLength': 1, 'maxLength': 160},
            'group_by': {'type': 'string', 'minLength': 1, 'maxLength': 100},
            'metric': {'type': 'string', 'enum': ['count', 'sum'], 'default': 'count'},
            'value_field': {'type': 'string', 'minLength': 1, 'maxLength': 100,
                            'description': 'Sum: numeric quantity, not ID/date.'},
        }, 'required': ['title']}},
    'filters': {'type': 'array', 'maxItems': 8, 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {'field': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                       'op': {'type': 'string', 'enum': ['equals', 'contains', 'minimum', 'maximum']},
                       'value': {'type': 'string', 'maxLength': 200}},
        'required': ['field', 'op', 'value']}},
}


class AnalyzeApiDataset(Tool):
    name = api_reports.INSPECT
    category = 'web'
    default_permission = 'auto'
    description = 'Inspect retained API labels for column paths, types, missing counts and coverage. Read-only.'
    parameters = {'type': 'object', 'additionalProperties': False,
                  'properties': {'labels': deepcopy(FIELDS['labels'])}, 'required': ['labels']}

    def run(self, ctx, **arguments):
        if next(Draft202012Validator(self.parameters).iter_errors(arguments), None):
            return ToolResult('Invalid dataset inspection. Supply source labels.', is_error=True)
        try:
            return ToolResult(api_reports.inspect(ctx, **arguments))
        except (DatasetError, ExtractionError) as exc:
            return ToolResult(str(exc), is_error=True)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            return ToolResult('Could not validate the requested analysis. Inspect columns and use supported types and finite decimal values.', is_error=True)


class CreateApiReport(Tool):
    name = api_reports.REPORT
    category = 'filesystem'
    default_permission = 'ask'
    description = ('Create report.html, tables/bar charts and CSV/JSON from retained API labels. Inspect columns first. '
        'Max 50 groups; omit group_by for overall. List counts overlap. Filters AND; contains ignores case.')
    parameters = {'type': 'object', 'additionalProperties': False,
                  'properties': {**deepcopy(FIELDS), 'title': {'type': 'string', 'minLength': 1, 'maxLength': 200}},
                  'required': ['labels', 'title', 'sections']}
    parameters['properties']['sections']['minItems'] = 1

    def run(self, ctx, **arguments):
        if next(Draft202012Validator(self.parameters).iter_errors(arguments), None):
            return ToolResult('Invalid report. Supply source labels, title and 1–6 bounded analysis sections.', is_error=True)
        try:
            artifacts, result = api_reports.report(ctx, **arguments)
        except (DatasetError, ExtractionError) as exc:
            return ToolResult(str(exc) + ' No completed report was published.', is_error=True)
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError):
            return ToolResult('Report validation or file publication failed. No completed report was reported; retained sources remain available.', is_error=True)
        return ToolResult('Created report.html and its reproducible data/analysis files. ' + json.dumps(result) +
            '\nComputed from ' + ' '.join(f'[{label}]' for label in arguments['labels']) +
            '. Tables and chart values share the same computed results; staged file bytes were verified. '
            'No live visual review was performed. Coverage refers to captured records, not independent upstream completeness.', artifacts=artifacts)
