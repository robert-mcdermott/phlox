"""Research notes and explicit handoff to approved analysis tools."""
import json
from app import research_notebook
from app.agent.tools.base import Tool, ToolResult


class UpdateResearchNotebook(Tool):
    name = research_notebook.NAME
    category = 'knowledge'
    default_permission = 'auto'
    description = ('Replace the current research notebook with concise source-linked findings, disagreements, '
                   'and unresolved questions. Preserve all still-relevant findings when updating. '
                   'Use after several reads, preferably in the same batch as the next search. '
                   'Notes summarize evidence; they are not new sources or private reasoning.')
    parameters = research_notebook.SCHEMA

    def run(self, ctx, findings=None, disagreements=None, questions=None, **_):
        error = research_notebook.update(ctx, dict(findings=findings, disagreements=disagreements, questions=questions))
        return ToolResult(error or 'Research notebook updated. Earlier completed research exchanges may now be condensed.', is_error=bool(error))


class BeginResearchAnalysis(Tool):
    name = 'begin_research_analysis'
    category = 'filesystem'
    default_permission = 'ask'
    description = ('Enable workspace/code tools for requested Research analysis or custom charts. '
                   'Normal execution approvals still apply. Code uses configured sandbox/network, '
                   'not the research fetch domain filter. Declare relative output_paths for delivery checks.')
    parameters = {'type': 'object', 'additionalProperties': False, 'properties': {
        'purpose': {'type': 'string', 'minLength': 1, 'maxLength': 500},
        'output_paths': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'uniqueItems': True,
                         'items': {'type': 'string', 'minLength': 1, 'maxLength': 300}},
    }, 'required': ['purpose', 'output_paths']}

    def run(self, ctx, **arguments):
        from jsonschema import Draft202012Validator
        from app import research_analysis
        if (not ctx.research or ctx.research.phase != 'gather'
                or next(Draft202012Validator(self.parameters).iter_errors(arguments), None)
                or any(not research_analysis.valid_path(p) for p in arguments['output_paths'])):
            return ToolResult('Supply a purpose and relative output paths during Research gathering.', is_error=True)
        ctx.research.state.update(analysis_enabled=True, analysis_outputs=arguments['output_paths'])
        files = research_analysis.available_files(ctx)
        names = research_analysis.TOOLS & (ctx.allowed_tools or set())
        return ToolResult('Research analysis enabled. Eligible tools: ' + ', '.join(sorted(names)) + '. '
                          + research_analysis.capabilities() + ' Existing files (use these exact paths; no extra export or file search): '
                          + json.dumps(files) + '. Reuse existing reports in output_paths; declare only additional files still needed. '
                          'Verify requested outputs before the final report. File existence checks do not verify scientific correctness.')
