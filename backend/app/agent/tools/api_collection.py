"""Collect a bounded sequence of validated API pages and publish a dataset bundle."""
from jsonschema import Draft202012Validator

from app import api_collection, api_dataset, web_fetch
from app.agent.tools.base import Tool, ToolResult
from app.web_extract_worker import ExtractionError


class CollectApiDataset(Tool):
    name = api_collection.NAME
    category = 'filesystem'
    default_permission = 'ask'
    description = ('When data files are requested, start with query_public_api to inspect a small page, then collect '
                   'more pages and export CSV/JSON files in one call. Supply labels for ALL retained pages from offset zero, '
                   'in order. Continuation reuses those pages without refetching. max_pages limits additional page attempts; '
                   'max_records limits total retained records. Uses the saved filters/page size and shared retries. '
                   'Each new page consumes a Research read. Returns compact counts, source labels and files, not raw records. '
                   'Partial results remain explicit. Stop retains saved sources but creates no new files. '
                   'No arbitrary URLs, code, full articles or study-detail expansion.')
    parameters = {'type': 'object', 'additionalProperties': False, 'properties': {
        'labels': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'uniqueItems': True,
                   'items': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$'}},
        'max_pages': {'type': 'integer', 'minimum': 1, 'maximum': api_collection.MAX_PAGES, 'default': 5},
        'max_records': {'type': 'integer', 'minimum': 1, 'maximum': api_collection.MAX_RECORDS, 'default': 200},
        'max_seconds': {'type': 'integer', 'minimum': 1, 'maximum': api_collection.MAX_SECONDS, 'default': 60},
    }, 'required': ['labels']}

    def run(self, ctx, **arguments):
        if next(Draft202012Validator(self.parameters).iter_errors(arguments), None):
            return ToolResult('Supply ordered retained API labels and valid max_pages/max_records/max_seconds limits.', is_error=True)
        try:
            content, artifacts, stopped = api_collection.run(ctx, **arguments)
            return ToolResult(content, artifacts=artifacts, is_error=stopped)
        except (api_dataset.DatasetError, ExtractionError, web_fetch.FetchError) as exc:
            return ToolResult(str(exc) + ' No completed collection export reported; previously saved pages remain retained.', is_error=True)
        except (ValueError, KeyError, TypeError, OSError, ArithmeticError):
            return ToolResult('Collection could not validate its pages or publish files. Previously saved pages remain retained; no completed export reported.', is_error=True)
