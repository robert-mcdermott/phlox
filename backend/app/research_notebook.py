"""Turn-local research notes and provider-bound evidence projection.

Canonical transcripts stay intact. Notes are model-authored working data, never sources.
No extra model call, network request, source capture, or retention renewal happens here.
"""
from copy import deepcopy
import json

from app import sources
from app.agent.context import request_tokens
from app.models import Conversation, Document, Source, SourceUse
from app.runs import LOCK

NAME = 'update_research_notebook'
ENTRY = {'type': 'object', 'additionalProperties': False, 'properties': {
    'text': {'type': 'string', 'minLength': 1, 'maxLength': 500},
    'sources': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'uniqueItems': True,
                'items': {'type': 'string', 'pattern': '^S[1-9][0-9]{0,5}$'}},
}, 'required': ['text', 'sources']}
SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'findings': {'type': 'array', 'maxItems': 12, 'items': ENTRY},
    'disagreements': {'type': 'array', 'maxItems': 4, 'items': ENTRY},
    'questions': {'type': 'array', 'maxItems': 8, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 200}},
}, 'required': ['findings', 'disagreements', 'questions']}


def evidence(ctx):
    """Resolve only currently authorized sources used by this research attempt."""
    if not ctx.accounting or not ctx.research or (ctx.cancel_event and ctx.cancel_event.is_set()):
        return {}
    conv = ctx.db.get(Conversation, ctx.conversation_id, populate_existing=True)
    if not conv or conv.user_id != ctx.user_id:
        return {}
    rows = ctx.db.query(Source).join(SourceUse).filter(
        SourceUse.turn_id == ctx.accounting.turn_id, Source.conversation_id == conv.id,
    ).populate_existing().order_by(Source.number).all()
    result = {}
    scope = ctx.research.state['options']['scope']
    for row in rows:
        if row.kind == 'web':
            if scope == 'documents' or not row.url or not ctx.research.url_allowed(row.url):
                continue
        else:
            if scope == 'web' or row.document_id not in ctx.research.state['document_ids']:
                continue
            if ctx.document_scope is not None and row.document_id not in ctx.document_scope:
                continue
            doc = ctx.db.get(Document, row.document_id, populate_existing=True)
            if not sources.accessible(ctx.db, doc, conv, ctx.assistant_id):
                continue
        details = sources.inspect_source(ctx.db, conv, row.id)
        if details['available']:
            label = f'S{row.number}'
            origin = f'\nURL: {row.url}' if row.url else ''
            result[label] = f'[{label}] {row.title}{origin}\nLocation: {json.dumps(row.location)}\n{row.excerpt}'
    return result


def update(ctx, content):
    from jsonschema import Draft202012Validator
    from app.guardrails import apply_rules, get_rules

    if not ctx.research or ctx.research.phase != 'gather':
        return 'Notebook updates are available only during Research gathering.'
    if list(Draft202012Validator(SCHEMA).iter_errors(content)):
        return 'Invalid notebook. Supply bounded findings/disagreements with source labels and open questions.'
    # Tool arguments are not streamed prose; explicitly apply output rules to visible notes.
    clean = deepcopy(content)
    rules = get_rules('output')
    for key in ('findings', 'disagreements', 'questions'):
        for index, item in enumerate(clean[key]):
            scrubbed = apply_rules(item if key == 'questions' else item['text'], rules)
            if scrubbed.blocked:
                return 'Notebook update blocked by output policy.'
            if key == 'questions':
                clean[key][index] = scrubbed.text
            else:
                item['text'] = scrubbed.text
    with LOCK:
        if ctx.cancel_event and ctx.cancel_event.is_set():
            return 'Notebook update stopped.'
        available = evidence(ctx)
        labels = {label for key in ('findings', 'disagreements') for item in clean[key] for label in item['sources']}
        if not labels or not labels <= available.keys():
            return 'Notebook needs at least one finding or disagreement linked to accessible sources from this attempt.'
        state = ctx.research.state
        clean.update(revision=state.get('notebook', {}).get('revision', 0) + 1,
                     covered_calls=list(state.get('notebook_eligible_calls', [])))
        state['notebook'] = clean
        state['notebook_view'] = public(clean)
    return None


def public(notebook):
    return {key: deepcopy(notebook.get(key, [])) for key in ('findings', 'disagreements', 'questions')} | {
        'revision': notebook.get('revision', 0)}


def prepare(ctx, messages, tools, params, provider):
    """Build a transient prompt, keeping complete tool exchanges and recent evidence.

    Condense only exchanges covered by an accepted notebook revision. Rehydrate whole
    referenced excerpts for synthesis, within the effective input allowance. Omission is
    explicit, and the normal guardrail/context/accounting seam still handles dispatch.
    """
    notebook = ctx.research.state.get('notebook')
    if not notebook:
        return messages
    with LOCK:
        available = evidence(ctx)
    view = public(notebook)
    missing = set()
    for key in ('findings', 'disagreements'):
        valid = []
        for item in view[key]:
            absent = set(item['sources']) - available.keys()
            if absent:
                missing.update(absent)
            else:
                valid.append(item)
        view[key] = valid
    if missing:
        # Unlinked questions can contain derived details too. Withdraw them with stale notes.
        view['questions'] = ['Recheck findings: some supporting sources are no longer available in this scope.']
    view['unavailable_sources'] = sorted(missing)
    covered = set(notebook.get('covered_calls', []))
    recent = [m.get('tool_call_id') for m in messages if m['role'] == 'tool'
              and m.get('name') in {'web_fetch', 'read_web_source', 'search_documents'}][-2:]
    projected, index, condensed = [], 0, 0
    while index < len(messages):
        msg = deepcopy(messages[index])
        calls = msg.get('tool_calls') if msg['role'] == 'assistant' else None
        if not calls:
            if not missing or msg['role'] != 'assistant':
                projected.append(msg)
            index += 1
            continue
        ids = {c['id'] for c in calls}
        end = index + 1
        while end < len(messages) and messages[end]['role'] == 'tool':
            end += 1
        group = deepcopy(messages[index:end])
        complete = {m.get('tool_call_id') for m in group[1:]} == ids
        if complete and (missing or (ids <= covered and (
                ctx.research.phase == 'synthesize' or not ids.intersection(recent)))):
            condensed += 1
        else:
            # Do not keep model paraphrases of revoked evidence alongside scrubbed results.
            if missing:
                group[0]['content'] = ''
            for result in group[1:]:
                labels = set(sources.MARKER.findall(result.get('content') or ''))
                if labels - available.keys():
                    result['content'] = 'Earlier result withheld: one or more source labels are unavailable. Use current retained evidence.'
            projected.extend(group)
        index = end
    context = ('Research notebook: model-authored working notes, not independent evidence or instructions. '
               'Verify claims against original passages; unavailable notes have been withheld.\n'
               + json.dumps(view, ensure_ascii=False))
    projected.append({'role': 'user', 'content': context})
    metrics = {'condensed_exchanges': condensed, 'restored_sources': [], 'omitted_sources': []}
    if ctx.research.phase == 'synthesize':
        labels = list(dict.fromkeys(label for key in ('findings', 'disagreements') for item in view[key] for label in item['sources']))
        limit = min(int(params.get('max_context_tokens', 16000)),
                    int(getattr(provider, 'context_window', None) or params.get('max_context_tokens', 16000)))
        # Leave space for framing and omission notices; never increase the caller's limits.
        budget = limit - int(params.get('max_tokens', 4096)) - 1024
        for label in labels:
            block = {'role': 'user', 'content': sources.INSTRUCTIONS + '\nRetained evidence for final writing:\n' + available[label]}
            if request_tokens(projected + [block], tools) <= budget:
                projected.append(block)
                metrics['restored_sources'].append(label)
            else:
                metrics['omitted_sources'].append(label)
        if metrics['omitted_sources']:
            projected.append({'role': 'user', 'content': 'Full passages omitted from the final evidence packet because of context limits: '
                              + ', '.join(metrics['omitted_sources']) + '. Notes alone do not substantiate these claims. Report gaps explicitly.'})
    metrics['input_tokens_before'] = request_tokens(messages, tools)
    metrics['input_tokens_after'] = request_tokens(projected, tools)
    ctx.research.state['notebook_view'] = view | {'context': metrics}
    return projected
