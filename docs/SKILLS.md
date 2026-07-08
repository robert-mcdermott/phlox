# Agent Skills

Skills are named, reusable markdown instructions that teach the agent a specialized
workflow — "how we do data analysis here", "how to write a research report", "how to build
a single-file web app". They follow the [Anthropic Agent Skills](https://github.com/anthropics/skills)
model and are import/export-compatible with its `SKILL.md` format, adapted to Phlox's
database-backed, multi-user architecture.

A skill is two things:

- a **description** — what the skill does *and when to use it*. This is the activation
  trigger: it's all the model sees until the skill is actually loaded.
- **instructions** — the full markdown workflow the model follows once the skill is active.

Phlox seeds three example skills on first boot (`data-analysis`, `deep-research`,
`web-app`); edit or delete them freely.

## Using skills

### Explicit: slash commands

Type `/` at the start of the composer to open the skill picker (arrow keys + Enter, or
click). The chosen skill appears as a chip above the composer; the rest of your text is
the actual message:

```
/deep-research what changed in the EU AI Act this year?
```

The skill's **full instructions are injected into the system prompt for that turn**, and
the message is recorded with the invocation so the history keeps an `[Invoked skills: …]`
marker (later turns don't re-pay the full instruction cost; the model can reload a skill
with `use_skill` if it needs it again).

### Automatic: the agent picks (progressive disclosure)

When the **Skills** toggle under the composer is on (the default), the system prompt
lists every auto-activatable skill as *name + description only*, and the model gets a
`use_skill` tool to load a skill's full instructions when a request matches its trigger.
Unused skills cost almost no context — this is the same progressive-disclosure design
Claude Code uses.

Turn the toggle off to hide the listing and the tool for that message. Per-skill, the
**"Agent may auto-activate"** flag controls whether a skill is advertised at all — turn it
off for skills that should only ever run when explicitly invoked with `/`.

`use_skill` is a regular tool: it appears in the admin **Tools** panel with the usual
`auto | ask | deny` permission policy, and assistants with the *Agent tools* capability
disabled never see it (explicit `/` invocation still works there — it's just prompt text).

## Managing skills

**Settings → Skills.** Every user can create, edit, and delete their own skills
(`private` — visible and invocable only by them). Admins can additionally publish
**public** skills available to everyone, and manage all skills.

| Field | Meaning |
|---|---|
| Name | Slug handle (`data-analysis`); what users type after `/`. Unique, auto-slugified. |
| Description | What it does + when to use it. Drives auto-activation; make it specific. |
| Instructions | Markdown workflow followed once active. |
| Agent may auto-activate | Advertise to the model for self-serve loading. |
| Visibility | `private` (creator only) / `public` (admin-only setting). |

### Import / export (SKILL.md)

Skills interoperate with the [Agent Skills format](https://github.com/anthropics/skills):
YAML frontmatter (`name`, `description`) + markdown body. **Import SKILL.md** in the
Skills panel accepts any such file — including skills from the community ecosystem — and
each skill row has an export button that downloads it back in the same format.

```markdown
---
name: commit-poet
description: Write commit messages as haiku. Use when asked for a commit message.
---

# Commit Poet

Write every commit message as a haiku (5-7-5).
```

One deliberate difference from Claude Code's skills: Phlox skills are **instructions
only** — bundled resource files/scripts aren't stored with the skill. The agent's
sandboxed workspace tools (`write_file`, `execute_python`, …) let a skill *create* any
scripts it needs at run time instead.

## Writing good skills

- Put the *when* in the description — the model decides to activate on the description
  alone. "Analyze data" triggers poorly; "Use when the user asks to analyze, profile, or
  find patterns in a dataset" triggers well.
- Write instructions as a numbered workflow with hard rules ("never state a number that
  isn't in executed output"), not prose.
- Keep instructions focused; a skill that tries to cover everything activates for nothing.
- Skills are injected into the system prompt, so they consume context — the caps are
  1,000 chars for descriptions and 60,000 for instructions, but shorter is better.

## How it works (internals)

- `backend/app/models.py` — `Skill` table (slug, description, instructions,
  `auto_activate`, `visibility`, `created_by`).
- `backend/app/skills.py` — SKILL.md parse/render, visibility queries, the two prompt
  builders, first-boot seeds.
- `backend/app/routers/skills.py` — `/api/skills` CRUD + `/import` + `/{id}/export`.
- `backend/app/agent/tools/skills.py` — the `use_skill` tool.
- `backend/app/routers/chat.py` — per-turn injection: full instructions for `/`-invoked
  skills, the name+description listing for auto-activation, and the `use_skill`
  enable/disable decision.
- Frontend: `Composer.jsx` (slash picker, chips, Skills toggle),
  `settings/SkillsPanel.jsx` (management UI).

Visibility is enforced server-side on every path (listing, `/` invocation, `use_skill`):
a user can never activate another user's private skill.
