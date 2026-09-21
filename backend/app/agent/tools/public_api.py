"""Cited public API reading, through explicitly scoped adapters only."""
import uuid

from jsonschema import Draft202012Validator

from app import public_api, sources, web_fetch
from app.agent.tools.base import Tool, ToolResult
from app.models import Conversation


class QueryPublicApi(Tool):
    name = public_api.NAME
    category = 'web'
    default_permission = 'auto'
    description = (
        'Query a supported public read-only API and capture one cited page. Currently supports NIH RePORTER '
        'parent projects by organization name and fiscal year, using its documented POST search. '
        'Start with org_names/fiscal_years and a small limit. Continue with only continue_from=S# to reuse '
        'the saved filters and next offset. No arbitrary endpoints or raw POST bodies. '
        'Pages are partial datasets, not annual totals; verify organizations, funding scope and completeness.'
    )
    parameters = public_api.PARAMETERS

    def run(self, ctx, **arguments):
        if next(Draft202012Validator(self.parameters).iter_errors(arguments), None):
            return ToolResult('Invalid API query. Supply org_names and fiscal_years (optional limit), or only continue_from. '
                              'Only adapter nih_projects is supported; URLs, headers and raw bodies are not accepted.', is_error=True)
        turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
        try:
            if ctx.cancel_event and ctx.cancel_event.is_set():
                raise web_fetch.FetchError('cancelled', 'API query stopped.')
            # Check source capacity before any network work, also in ordinary Chat.
            conv = ctx.db.get(Conversation, ctx.conversation_id, populate_existing=True)
            if not conv or conv.user_id != ctx.user_id or sources.remaining_capacity(ctx.db, ctx.conversation_id, turn_id) < 1:
                return ToolResult('API evidence unavailable: conversation or source allowance unavailable.', is_error=True)
            request, previous = (public_api.continuation(ctx, arguments['continue_from'], turn_id)
                                 if arguments.get('continue_from') else (public_api.recipe(arguments), None))
            page, status, digest, request_hash = public_api.query(ctx, request, previous)
            location = {'format': 'api', 'adapter': 'nih_projects', 'method': 'POST', 'request': request,
                        'request_hash': request_hash, 'offset': page['offset'], 'item_end': page['end'],
                        'total_records': page['total'], 'next_offset': page['next_offset'],
                        'window_exhausted': page['window_exhausted'], 'record_ids': page['ids']}
            captures = sources.capture_web(ctx.db, conversation_id=ctx.conversation_id, user_id=ctx.user_id,
                turn_id=turn_id, url=public_api.ENDPOINT, title='NIH RePORTER project query', text=page['text'],
                content_hash=digest, http_status=status, cancel=ctx.cancel_event, provenance=location)
            if not captures or not captures[0].startswith('[S'):
                return ToolResult('API query stopped or evidence could not be retained.', is_error=True)
            if ctx.research:
                ctx.research.state['api_data_available'] = True
            notice = ('Selected project fields; other fields are omitted. Name fragments may match multiple organizations. '
                      'Parent projects only; null award amounts are unknown, not zero. RePORTER includes NIH and non-NIH '
                      'agency projects. Do not sum these records as NIH-only funding without verifying agency scope. '
                      'A page is not a complete dataset or a verified annual total. ')
            if page['window_exhausted']:
                notice += 'API offset window exhausted before the reported total. Narrow the organization/year query. '
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
