"""Auth endpoints: login, register, me, and admin user management."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import entra, service
from app.auth.deps import get_current_user, require_admin
from app.auth.security import create_access_token
from app.config import get_auth_config
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class UserOut(BaseModel):
    id: str
    username: str
    email: str | None = None
    display_name: str | None = None
    department: str | None = None
    role: str
    auth_provider: str
    is_active: bool
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str
    user: UserOut


class RegisterIn(BaseModel):
    username: str
    password: str
    email: str | None = None
    display_name: str | None = None


class CreateUserIn(RegisterIn):
    role: str = "user"
    department: str | None = None


class UpdateUserIn(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None
    display_name: str | None = None
    department: str | None = None


@router.get("/config")
def auth_config():
    """Public: what the login screen needs to render."""
    cfg = get_auth_config()
    return {
        "enabled": cfg["enabled"],
        "allow_registration": cfg["allow_registration"],
        "entra_enabled": entra.is_enabled(),
    }


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = service.authenticate_local(db, body.username, body.password)
    if not user:
        raise HTTPException(401, "Invalid username or password")
    return {"token": create_access_token(user.id, user.role), "user": user}


@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    cfg = get_auth_config()
    if not cfg["allow_registration"]:
        raise HTTPException(403, "Registration is disabled")
    if service.get_by_username(db, body.username):
        raise HTTPException(409, "Username already taken")
    # First registered user becomes admin if none exist.
    role = "admin" if db.query(User).count() == 0 else "user"
    user = service.create_user(
        db, username=body.username, password=body.password, role=role,
        email=body.email, display_name=body.display_name,
    )
    return {"token": create_access_token(user.id, user.role), "user": user}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# --- Entra (SSO) ---------------------------------------------------------
@router.get("/entra/login")
def entra_login(state: str = "phlox"):
    if not entra.is_enabled():
        raise HTTPException(404, "Entra SSO is not configured")
    return {"authorize_url": entra.authorize_url(state)}


@router.get("/entra/callback", response_model=TokenOut)
def entra_callback(code: str, db: Session = Depends(get_db)):
    if not entra.is_enabled():
        raise HTTPException(404, "Entra SSO is not configured")
    user = entra.exchange_code_and_upsert(db, code)
    return {"token": create_access_token(user.id, user.role), "user": user}


# --- Admin user management ----------------------------------------------
@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.created_at).all()


@router.post("/users", response_model=UserOut)
def create_user(body: CreateUserIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if service.get_by_username(db, body.username):
        raise HTTPException(409, "Username already taken")
    return service.create_user(
        db, username=body.username, password=body.password, role=body.role,
        email=body.email, display_name=body.display_name, department=body.department,
    )


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: str, body: UpdateUserIn,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.department is not None:
        user.department = body.department
    if body.password:
        service.set_password(db, user, body.password)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(400, "You cannot delete your own account")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    # Purge the user's private data (chats, workspaces, documents, memories, settings),
    # then remove the account. The admin never reads the content — it's just deleted.
    purged = service.delete_user_data(db, user_id)
    db.delete(user)
    db.commit()
    return {"deleted": user_id, "purged": purged}
