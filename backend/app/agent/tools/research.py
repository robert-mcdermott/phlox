"""Research-only, turn-local working notes; never writes workspace files."""
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
