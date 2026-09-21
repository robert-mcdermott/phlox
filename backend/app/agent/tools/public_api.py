"""Cited public API reading, through explicitly scoped adapters only."""
import uuid
from copy import deepcopy

from jsonschema import Draft202012Validator

from app import public_api, public_api_details, public_api_adapters as adapters, sources, web_fetch
from app.agent.tools.base import Tool, ToolResult
from app.models import Conversation


class QueryPublicApi(Tool):
    name = public_api.NAME
    category = 'web'
    default_permission = 'auto'
    description = (
        'Query NIH RePORTER projects (org_names/fiscal_years), or PubMed publications '
        '(adapter=pubmed, query with PubMed field/date tags). Capture one cited page. '
        'Start with a small limit (NIH default 5; PubMed default 2). Continue with only '
        'continue_from=S# to reuse the saved query. PubMed search captures bibliographic metadata, '
        'not study findings. To read a selected PubMed article, supply record_from=S#, record_id=PMID, '
        'and section=abstract (default) or authors. For authors optionally filter affiliation and set limit. '
        'Use start for later detail passages; using a detail citation as record_from checks version consistency. '
        'Reread saved evidence with read_web_source. No arbitrary URLs/POST bodies. Pages are partial '
        'datasets; inspect returned scope and completeness before analysis.'
    )
    parameters = public_api.PARAMETERS
    # Claude-backed endpoints reject top-level composition keywords. Keep the complete
    # union for dispatch/direct-call validation; advertise only its shared object fields.
    advertised_parameters = deepcopy({k: v for k, v in parameters.items() if k != 'oneOf'})

    def run(self, ctx, **arguments):
        if next(Draft202012Validator(self.parameters).iter_errors(arguments), None):
            return ToolResult('Invalid API query. Supply NIH org_names/fiscal_years, or adapter=pubmed and query, '
                              'with optional limit; continue with only continue_from. For details use record_from/record_id and section. '
                              'URLs, headers and raw bodies are not accepted.', is_error=True)
        turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
        try:
            if ctx.cancel_event and ctx.cancel_event.is_set():
                raise web_fetch.FetchError('cancelled', 'API query stopped.')
            # Check source capacity before any network work, also in ordinary Chat.
            conv = ctx.db.get(Conversation, ctx.conversation_id, populate_existing=True)
            if not conv or conv.user_id != ctx.user_id or sources.remaining_capacity(ctx.db, ctx.conversation_id, turn_id) < 1:
                return ToolResult('API evidence unavailable: conversation or source allowance unavailable.', is_error=True)
            if arguments.get('record_from'):
                return ToolResult(public_api_details.read(ctx, arguments, turn_id))
            if arguments.get('continue_from'):
                adapter, request, previous = public_api.continuation(ctx, arguments['continue_from'], turn_id,
                                                                    arguments.get('adapter'))
            else:
                adapter = adapters.get(arguments.get('adapter', 'nih_projects'))
                request, previous = public_api.recipe(arguments), None
            page, status, digest, request_hash = public_api.query(ctx, request, previous, adapter.name)
            location = {'format': 'api', 'adapter': adapter.name, 'method': adapter.method, 'request': request,
                        'request_hash': request_hash, 'offset': page['offset'], 'item_end': page['end'],
                        'total_records': page['total'], 'next_offset': page['next_offset'],
                        'window_exhausted': page['window_exhausted'], 'record_ids': page['ids']}
            if adapter.name == 'pubmed':
                location['query_translation'] = page['query_translation']
            captures = sources.capture_web(ctx.db, conversation_id=ctx.conversation_id, user_id=ctx.user_id,
                turn_id=turn_id, url=adapter.endpoint, title=adapter.title, text=page['text'],
                content_hash=digest, http_status=status, cancel=ctx.cancel_event, provenance=location)
            if not captures or not captures[0].startswith('[S'):
                return ToolResult('API query stopped or evidence could not be retained.', is_error=True)
            if ctx.research:
                ctx.research.state['api_data_available'] = True
            notice = adapter.notice + ' '
            if adapter.name == 'nih_projects':
                notice += 'Parent projects only; null award amounts are unknown, not zero. '
            if page['window_exhausted']:
                notice += 'API offset window exhausted before the reported total. Narrow the query. '
            elif page['next_offset'] is not None:
                notice += 'More API records remain. Use continue_from with this page\'s S-label for the next page, without filters or limit. '
            else:
                notice += 'End of this API query according to its reported total; this does not validate statistical completeness. '
            return ToolResult(sources.INSTRUCTIONS + '\n' + notice + '\n\n' + '\n\n'.join(captures))
        except web_fetch.FetchError as exc:
            # Failed queries never mint a usable pagination cursor or retain unvalidated rows.
            if exc.status == 'cancelled' or (ctx.cancel_event and ctx.cancel_event.is_set()):
                return ToolResult('API query stopped. No new evidence captured.', is_error=True)
            return ToolResult(str(exc) + ' No new evidence captured. Reuse retained sources or revise the query.', is_error=True)
