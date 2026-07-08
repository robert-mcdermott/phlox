"""Agent skills: reusable markdown instructions, SKILL.md-compatible.

A skill is a named set of markdown instructions (plus a trigger ``description``) stored in
the :class:`~app.models.Skill` table. This module holds everything that isn't a route:
slug/format helpers, SKILL.md (YAML frontmatter) parse/render for interop with the
Anthropic Agent Skills format, visibility-scoped queries, the two prompt builders
(explicit invocation + progressive-disclosure listing), and the first-boot seeds.
"""
from __future__ import annotations

import logging
import re

import yaml
from sqlalchemy.orm import Session

from app.models import Skill

logger = logging.getLogger(__name__)

#: hard caps so a pathological skill can't blow up the system prompt
MAX_NAME_LEN = 100
MAX_DESCRIPTION_LEN = 1_000
MAX_INSTRUCTIONS_LEN = 60_000


def slugify(name: str) -> str:
    """Normalize a skill name to its slash-command slug ("Data Analysis!" -> "data-analysis")."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:MAX_NAME_LEN]


# -- SKILL.md interop ---------------------------------------------------------
def parse_skill_md(text: str) -> dict:
    """Parse an Agent Skills SKILL.md file (YAML frontmatter + markdown body).

    Returns ``{name, description, instructions}``. Raises ``ValueError`` on files that
    don't carry the required frontmatter fields.
    """
    match = re.match(r"\A\s*---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.DOTALL)
    if not match:
        raise ValueError("Not a SKILL.md file: missing '---' YAML frontmatter block")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter: {e}") from e
    if not isinstance(meta, dict):
        raise ValueError("Invalid frontmatter: expected a YAML mapping")
    name = slugify(str(meta.get("name") or ""))
    description = str(meta.get("description") or "").strip()
    if not name:
        raise ValueError("Frontmatter is missing the required 'name' field")
    if not description:
        raise ValueError("Frontmatter is missing the required 'description' field")
    return {
        "name": name,
        "description": description[:MAX_DESCRIPTION_LEN],
        "instructions": match.group(2).strip()[:MAX_INSTRUCTIONS_LEN],
    }


def render_skill_md(skill: Skill) -> str:
    """Render a skill back to the portable SKILL.md format (for export)."""
    meta = yaml.safe_dump(
        {"name": skill.name, "description": skill.description},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    return f"---\n{meta}\n---\n\n{skill.instructions}\n"


# -- queries ------------------------------------------------------------------
def visible_skills(db: Session, user_id: str | None, *, include_inactive: bool = False) -> list[Skill]:
    """Active skills this user may see/invoke: public ones + their own."""
    q = db.query(Skill)
    if not include_inactive:
        q = q.filter(Skill.is_active.is_(True))
    q = q.filter((Skill.visibility == "public") | (Skill.created_by == user_id))
    return q.order_by(Skill.name).all()


def resolve_skill(db: Session, name: str, user_id: str | None) -> Skill | None:
    """Look up one visible, active skill by slug. Visibility is the security boundary:
    never inject a skill this user couldn't list."""
    s = db.query(Skill).filter(Skill.name == slugify(name), Skill.is_active.is_(True)).first()
    if not s or (s.visibility != "public" and s.created_by != user_id):
        return None
    return s


# -- prompt builders ----------------------------------------------------------
INVOKED_SKILL_PROMPT = """

The user explicitly invoked the skill "{name}" (/{name}) for the current message. Follow
these skill instructions for this turn; they take precedence over your general approach
(but never over safety or system rules):

<skill name="{name}">
{instructions}
</skill>
"""

SKILLS_PREAMBLE = """

## Skills
Registered skills teach you specialized workflows. Each entry below is name: trigger.
When the user's request matches a skill's trigger, call the `use_skill` tool with the
skill's name to load its full instructions BEFORE attempting the task. Load only the
skills you actually need for the current request; skip them for unrelated requests.

Available skills:
{listing}
"""


def invoked_skills_prompt(skills: list[Skill]) -> str:
    """Full-instruction blocks for skills the user explicitly invoked this turn."""
    return "".join(
        INVOKED_SKILL_PROMPT.format(name=s.name, instructions=s.instructions) for s in skills
    )


def skills_preamble(db: Session, user_id: str | None, *, exclude: set[str] = frozenset()) -> str:
    """The progressive-disclosure listing (name + description only) for auto-activation.

    Returns "" when there is nothing to advertise, so callers can also use it to decide
    whether to expose the ``use_skill`` tool at all.
    """
    rows = [
        s for s in visible_skills(db, user_id)
        if s.auto_activate and s.name not in exclude
    ]
    if not rows:
        return ""
    listing = "\n".join(f"- {s.name}: {s.description}" for s in rows)
    return SKILLS_PREAMBLE.format(listing=listing)


# -- first-boot seeds ---------------------------------------------------------
_SEED_SKILLS: list[dict] = [
    {
        "name": "data-analysis",
        "description": (
            "Structured exploratory data analysis of an uploaded or generated dataset "
            "(CSV/JSON/Excel). Use when the user asks to analyze, profile, or find "
            "patterns/trends in data, or asks questions a dataset in the workspace can answer."
        ),
        "instructions": """# Data Analysis

Run a disciplined, reproducible analysis with `execute_python`. Never eyeball raw data and
guess — compute.

## Workflow
1. **Profile first.** Load the data with pandas and print: shape, dtypes, null counts,
   duplicates, and `describe()` for numeric columns. State data-quality caveats up front.
2. **Frame the question.** Restate the user's question as one or more measurable
   quantities before writing analysis code.
3. **Analyze.** Prefer simple, verifiable aggregations (groupby, value_counts,
   correlations) over clever one-liners. Print intermediate results so the numbers in
   your answer are traceable to output.
4. **Visualize.** Save 1–3 matplotlib charts as PNG files in the workspace (they surface
   as artifacts). Label axes and titles; one message per chart.
5. **Report.** Lead with the answer, then the supporting numbers, then caveats
   (sample size, nulls dropped, assumptions made).

## Rules
- Never fabricate numbers — every figure you state must appear in executed output.
- If the dataset is missing or unreadable, say exactly what you need instead of inventing data.
""",
    },
    {
        "name": "deep-research",
        "description": (
            "Multi-source web research with citations. Use when the user asks for a "
            "researched answer, comparison, or report on a current/factual topic and web "
            "search is enabled for the turn."
        ),
        "instructions": """# Deep Research

Produce a sourced answer, not a from-memory essay. Requires the `web_search` and
`web_fetch` tools; if web search is not enabled this turn, say so and ask the user to
enable it rather than answering from memory.

## Workflow
1. **Decompose** the question into 2–5 concrete sub-questions.
2. **Search broadly**: run a distinct `web_search` per sub-question; prefer primary
   sources (docs, papers, official announcements) over aggregators.
3. **Read deeply**: `web_fetch` the 3–6 most promising results. Skim for the specific
   claims you need; note publication dates.
4. **Cross-check**: any load-bearing fact should appear in at least two independent
   sources, or be flagged as single-source.
5. **Write up**: synthesized answer first, then key findings with inline numbered
   citations, then a Sources list mapping numbers to URLs. Note disagreements between
   sources explicitly.

## Rules
- Cite every non-obvious claim; never cite a page you did not fetch.
- Prefer recent sources for fast-moving topics and say when information may be stale.
""",
    },
    {
        "name": "web-app",
        "description": (
            "Build a polished single-file HTML/CSS/JS web app or interactive visualization "
            "as an artifact. Use when the user asks for a small app, game, dashboard, "
            "calculator, or interactive demo."
        ),
        "instructions": """# Single-File Web App

Build the app as **one self-contained `.html` file** written to the workspace with
`write_file` — it renders live in the artifact canvas.

## Workflow
1. Confirm the core interaction in one sentence (in your head, not by asking) and pick
   the simplest structure that supports it. No build steps, no frameworks.
2. Write the full file in one `write_file` call: inline `<style>` and `<script>`,
   semantic HTML, no external network dependencies (CDNs may be blocked).
3. Test the logic that can be tested headlessly with `execute_node` (pure functions,
   game rules) before declaring it done.
4. Iterate with `edit_file` rather than rewriting the whole file for small fixes.

## Quality bar
- Responsive layout (flex/grid, relative units); usable on a narrow viewport.
- Respect `prefers-color-scheme` for dark/light.
- Keyboard support for anything clickable; visible focus states.
- Handle empty/error states — no dead buttons or NaN displays.
""",
    },
]


def seed_builtin_skills(db: Session) -> None:
    """Seed the example skills once (first boot, empty table). Users can edit/delete
    them freely afterwards — this never re-creates rows."""
    if db.query(Skill.id).first() is not None:
        return
    for spec in _SEED_SKILLS:
        db.add(Skill(**spec, visibility="public", created_by=None, auto_activate=True))
    db.commit()
    logger.info("Seeded %d example skills", len(_SEED_SKILLS))
