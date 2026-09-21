"""Collect a bounded sequence of validated API pages and publish a dataset bundle."""
from jsonschema import Draft202012Validator

from app import api_collection, api_dataset, bulk_datasets, web_fetch
from app.agent.tools.base import Tool, ToolResult
from app.web_extract_worker import ExtractionError


class CollectApiDataset(Tool):
    name = api_collection.NAME
    category = 'filesystem'
    default_permission = 'ask'
    description = ('Collect full API datasets after a small preview: supply source=S# at offset zero, '
                   'or dataset_id to resume. Bulk pages stay outside model context and citation limits. '
                   'Use one query for all requested years. Returns CSV/JSON, coverage and a checkpoint. '
                   'Partial data stays explicit. '
                   'Stop saves progress. Legacy labels uses small citation pages instead.')
    parameters = {'type': 'object', 'additionalProperties': False, 'properties': {
        'source': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$'},
        'dataset_id': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'},
        'labels': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'uniqueItems': True,
                   'items': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$'}},
        'max_pages': {'type': 'integer', 'minimum': 1, 'maximum': bulk_datasets.MAX_PAGES, 'default': 100},
        'max_records': {'type': 'integer', 'minimum': 1, 'maximum': bulk_datasets.MAX_RECORDS, 'default': 10000},
        'max_seconds': {'type': 'integer', 'minimum': 1, 'maximum': bulk_datasets.MAX_SECONDS, 'default': 300},
    }}

    def run(self, ctx, **arguments):
        if (next(Draft202012Validator(self.parameters).iter_errors(arguments), None)
                or sum(key in arguments for key in ('source', 'dataset_id', 'labels')) != 1):
            return ToolResult('Supply exactly one of source, dataset_id or legacy labels, with valid collection limits.', is_error=True)
        try:
            if 'labels' in arguments:
                for key, maximum in [('max_pages', api_collection.MAX_PAGES), ('max_records', api_collection.MAX_RECORDS), ('max_seconds', api_collection.MAX_SECONDS)]:
                    if arguments.get(key, 1) > maximum:
                        raise api_dataset.DatasetError('Legacy labels limits exceeded. Use source or dataset_id for bulk data.')
            collect = api_collection.run if 'labels' in arguments else bulk_datasets.run
            content, artifacts, stopped = collect(ctx, **arguments)
            return ToolResult(content, artifacts=artifacts, is_error=stopped)
        except (api_dataset.DatasetError, ExtractionError, web_fetch.FetchError) as exc:
            return ToolResult(str(exc) + ' No completed collection export reported; previously saved pages remain retained.', is_error=True)
        except (ValueError, KeyError, TypeError, OSError, ArithmeticError):
            return ToolResult('Collection could not validate its pages or publish files. Previously saved pages remain retained; no completed export reported.', is_error=True)
