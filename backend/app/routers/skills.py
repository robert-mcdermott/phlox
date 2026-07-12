"""Agent skills: CRUD + SKILL.md import/export.

Any authenticated user can create skills; non-admins are forced to ``private``
(visible/invocable only by them), admins can publish ``public`` skills for everyone.
``name`` is the global slash-command handle, so it is unique across the deployment.
Import/export speaks the Anthropic Agent Skills SKILL.md format (YAML frontmatter +
markdown body) for interop with the anthropics/skills ecosystem.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database import get_db
from app.models import Skill, User
from app.schemas import SkillCreate, SkillOut, SkillUpdate
from app.skills import (
    MAX_DESCRIPTION_LEN,
    MAX_INSTRUCTIONS_LEN,
    parse_skill_md,
    render_skill_md,
    slugify,
    visible_skills,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])

MAX_IMPORT_BYTES = 512 * 1024


def _visible(db: Session, skill_id: str, user: User) -> Skill:
    s = db.get(Skill, skill_id)
    if not s or (s.visibility != "public" and s.created_by != user.id):
        raise HTTPException(404, "Skill not found")
    return s


def _require_owner(db: Session, skill_id: str, user: User) -> Skill:
    s = _visible(db, skill_id, user)
    if s.created_by != user.id and not (user.role == "admin" and s.visibility == "public"):
        raise HTTPException(404, "Skill not found")
    return s


def _check_fields(name: str, description: str, instructions: str) -> str:
    """Normalize the slug and enforce the size caps. Returns the slug."""
    slug = slugify(name)
    if not slug:
        raise HTTPException(422, "Skill name must contain letters or digits")
    if len(description) > MAX_DESCRIPTION_LEN:
        raise HTTPException(422, f"Description too long (max {MAX_DESCRIPTION_LEN} chars)")
    if len(instructions) > MAX_INSTRUCTIONS_LEN:
        raise HTTPException(422, f"Instructions too long (max {MAX_INSTRUCTIONS_LEN} chars)")
    return slug


def _check_name_free(db: Session, slug: str, skill_id: str | None = None) -> None:
    q = db.query(Skill.id).filter(Skill.name == slug)
    if skill_id:
        q = q.filter(Skill.id != skill_id)
    if q.first() is not None:
        raise HTTPException(409, f"A skill named '{slug}' already exists")


def _effective_visibility(requested: str, user: User) -> str:
    # Publishing to everyone is an admin decision; silently keep user skills private.
    return requested if user.role == "admin" else "private"


def _create(db: Session, body: SkillCreate, user: User) -> Skill:
    slug = _check_fields(body.name, body.description, body.instructions)
    _check_name_free(db, slug)
    s = Skill(
        name=slug,
        description=body.description,
        instructions=body.instructions,
        auto_activate=body.auto_activate,
        visibility=_effective_visibility(body.visibility, user),
        created_by=user.id,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("", response_model=list[SkillOut])
def list_skills(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return visible_skills(db, user.id, include_inactive=True)


@router.post("", response_model=SkillOut)
def create_skill(
    body: SkillCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _create(db, body, user)


@router.post("/import", response_model=SkillOut)
async def import_skill(
    file: UploadFile, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Import an Agent Skills SKILL.md file (YAML frontmatter + markdown body)."""
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "Skill file too large")
    try:
        parsed = parse_skill_md(raw.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise HTTPException(422, "Skill file must be UTF-8 text") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _create(db, SkillCreate(**parsed), user)


@router.get("/{skill_id}", response_model=SkillOut)
def get_skill(skill_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _visible(db, skill_id, user)


@router.get("/{skill_id}/export", response_class=PlainTextResponse)
def export_skill(
    skill_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    s = _visible(db, skill_id, user)
    return PlainTextResponse(
        render_skill_md(s),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{s.name}-SKILL.md"'},
    )


@router.patch("/{skill_id}", response_model=SkillOut)
def update_skill(
    skill_id: str,
    body: SkillUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = _require_owner(db, skill_id, user)
    updates = body.model_dump(exclude_unset=True)
    if "visibility" in updates:
        updates["visibility"] = _effective_visibility(updates["visibility"], user)
    slug = _check_fields(
        updates.get("name", s.name),
        updates.get("description", s.description),
        updates.get("instructions", s.instructions),
    )
    if "name" in updates:
        _check_name_free(db, slug, skill_id=s.id)
        updates["name"] = slug
    for key, value in updates.items():
        setattr(s, key, value)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    s = _require_owner(db, skill_id, user)
    db.delete(s)
    db.commit()
    return {"deleted": skill_id}
