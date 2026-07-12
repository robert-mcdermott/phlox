"""Regression tests for secure first-run administrator setup."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_bootstrap_admin_gets_random_one_time_password(monkeypatch):
    from app import models
    from app.auth import service
    from app.auth.security import verify_password

    engine = create_engine("sqlite://", future=True)
    models.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        service,
        "get_auth_config",
        lambda: {"default_admin": {"username": "admin", "password": "admin"}},
    )

    with Session(engine) as db:
        bootstrap = service.seed_default_admin(db)
        assert bootstrap is not None
        assert bootstrap.username == "admin"
        assert len(bootstrap.password) == 32
        assert bootstrap.password != "admin"

        user = service.get_by_username(db, "admin")
        assert user is not None
        assert user.must_change_password is True
        assert verify_password(bootstrap.password, user.password_hash)
        assert not verify_password("admin", user.password_hash)

        # A restart never regenerates or reprints credentials for an existing database.
        assert service.seed_default_admin(db) is None


def test_startup_banner_uses_version_and_only_shows_new_credential():
    from app.auth.service import BootstrapAdmin
    from app.branding import get_version, render_startup_banner

    credential = BootstrapAdmin(username="admin", password="temporary-secret")
    first_run = render_startup_banner(credential, color=False)
    restart = render_startup_banner(color=False)

    assert "PHLOX" in first_run
    assert get_version() in first_run
    assert "FIRST-RUN ADMINISTRATOR" in first_run
    assert "temporary-secret" in first_run
    assert "temporary-secret" not in restart
    assert "FIRST-RUN ADMINISTRATOR" not in restart
    assert "\033[" in render_startup_banner(credential, color=True)


def test_auth_enabled_production_requires_strong_environment_secret(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "get_auth_config", lambda: {"enabled": True})
    monkeypatch.setenv("PHLOX_ENV", "production")
    monkeypatch.delenv("PHLOX_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="requires PHLOX_JWT_SECRET"):
        config.validate_auth_startup()

    monkeypatch.setenv("PHLOX_JWT_SECRET", "a" * 64)
    with pytest.raises(RuntimeError, match="requires PHLOX_JWT_SECRET"):
        config.validate_auth_startup()

    monkeypatch.setenv("PHLOX_JWT_SECRET", "5bC8gY7sR2qL9nW4xT6mK3pD8vF1hJ0z")
    config.validate_auth_startup()


def test_temporary_password_session_is_restricted_until_changed(client, db, monkeypatch):
    from app.auth import deps, service
    from app.models import User

    monkeypatch.setattr(deps, "get_auth_config", lambda: {"enabled": True})
    username = "bootstrap-flow-test"
    temporary_password = "temporary-password-123"
    new_password = "correct-horse-battery-staple"
    user = service.create_user(
        db,
        username=username,
        password=temporary_password,
        role="admin",
        must_change_password=True,
    )

    try:
        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": temporary_password},
        )
        assert login.status_code == 200
        assert login.json()["user"]["must_change_password"] is True
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        # Session inspection and password replacement work, but normal app data is gated.
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        blocked = client.get("/api/conversations", headers=headers)
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "Password change required"

        too_short = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": temporary_password, "new_password": "too-short"},
        )
        assert too_short.status_code == 422

        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": temporary_password, "new_password": new_password},
        )
        assert changed.status_code == 200
        assert changed.json()["must_change_password"] is False
        assert client.get("/api/conversations", headers=headers).status_code == 200

        db.expire_all()
        assert service.authenticate_local(db, username, temporary_password) is None
        assert service.authenticate_local(db, username, new_password) is not None
    finally:
        db.expire_all()
        persisted = db.get(User, user.id)
        if persisted:
            db.delete(persisted)
            db.commit()
