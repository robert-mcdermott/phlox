"""Write validated retained API evidence as bounded, reproducible dataset artifacts."""
from jsonschema import Draft202012Validator

from app import api_dataset
from app.agent.tools.base import Tool, ToolResult
from app.web_extract_worker import ExtractionError


class ExportApiDataset(Tool):
    name = api_dataset.NAME
    category = 'filesystem'
    default_permission = 'ask'
    description = ('When the user requests data files, export retained public API citation pages into records.csv, '
                   'records.json, summary.csv and manifest.json. Supply labels from one query. No network or arbitrary '
                   'code runs. Validates coverage and duplicates; NIH summaries use exact known award sums; partial data stays labelled '
                   'partial. PubMed pages contain bibliography; ClinicalTrials.gov pages contain study metadata. '
                   'Optional detail_labels add captured article/study sections in record_details.json. Creates a new workspace folder without overwriting files.')
    parameters = {'type': 'object', 'additionalProperties': False, 'properties': {
        'labels': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'uniqueItems': True,
                   'items': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$'}},
        'detail_labels': {'type': 'array', 'maxItems': 64, 'uniqueItems': True,
                          'items': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$'},
                          'description': 'Optional captured record-detail citations for records in labels; at most 64 total labels.'},
    }, 'required': ['labels']}

    def run(self, ctx, **arguments):
        if next(Draft202012Validator(self.parameters).iter_errors(arguments), None):
            return ToolResult('Supply 1–64 distinct API source labels in labels, for example ["S1", "S2"].', is_error=True)
        try:
            artifacts, coverage = api_dataset.export(ctx, arguments['labels'], arguments.get('detail_labels'))
        except (api_dataset.DatasetError, ExtractionError) as exc:
            return ToolResult(str(exc), is_error=True)
        except (ValueError, KeyError, TypeError, OSError, ArithmeticError):
            return ToolResult('Dataset export could not validate the saved pages or publish its files. No completed export was reported.', is_error=True)
        status = ('All API-reported matches captured' if coverage['all_reported_records_captured'] else 'Partial dataset')
        return ToolResult(f"{status}: {coverage['captured_unique_records']} unique records of "
                          f"{coverage['api_reported_matches']} reported matches. "
                          f"Removed {coverage['duplicate_records_removed']} identical overlapping records. "
                          'Created ' + ', '.join(a['name'] for a in artifacts) + '. ' + api_dataset.NOTICE, artifacts=artifacts)
