"""use_skill — the model-side half of skill auto-activation (progressive disclosure).

The system prompt lists auto-activatable skills as name + description only (see
``app.skills.skills_preamble``); this tool loads a skill's full instructions on demand so
unused skills cost no context. Visibility is enforced against the conversation owner
(``ctx.user_id``), the same boundary the /api/skills routes use.
"""
from __future__ import annotations

from app.agent.tools.base import Tool, ToolContext, ToolResult


class UseSkill(Tool):
    name = "use_skill"
    description = (
        "Load the full instructions of a registered skill by name. Call this before "
        "attempting a task that matches a skill listed under 'Skills' in your system "
        "prompt, then follow the returned instructions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill's registered name (e.g. 'data-analysis').",
            }
        },
        "required": ["name"],
    }
    category = "skills"
    default_permission = "auto"

    def run(self, ctx: ToolContext, name: str = "", **kwargs) -> ToolResult:
        from app.skills import resolve_skill, visible_skills

        skill = resolve_skill(ctx.db, name, ctx.user_id)
        if skill is None:
            available = ", ".join(s.name for s in visible_skills(ctx.db, ctx.user_id)) or "(none)"
            return ToolResult(
                content=f"Unknown skill '{name}'. Available skills: {available}",
                is_error=True,
            )
        return ToolResult(
            content=(
                f"Skill '{skill.name}' loaded. Follow these instructions for the "
                f"current task:\n\n{skill.instructions}"
            )
        )
