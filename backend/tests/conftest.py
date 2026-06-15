"""Pytest setup: isolate data/config to a temp dir BEFORE importing the app."""
import os
import tempfile
import textwrap
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="phlox-test-"))
os.environ["PHLOX_DATA"] = str(_TMP)
os.environ["PHLOX_JWT_SECRET"] = "test-secret-please-be-at-least-32-bytes-long!!"

_CFG = _TMP / "config.yml"
_CFG.write_text(
    textwrap.dedent(
        """
        auth:
          enabled: false
        default_profile: test
        profiles:
          test:
            type: openai
            label: Test
            endpoint: http://localhost:1/v1
            model: test-model
            supports_tools: true
        """
    ),
    encoding="utf-8",
)
os.environ["PHLOX_CONFIG"] = str(_CFG)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_app():
    from app.agent.registry import REGISTRY
    from app.agent.tools import register_builtin_tools
    from app.database import init_db

    init_db()
    if not REGISTRY.names():
        register_builtin_tools(REGISTRY)
    yield


@pytest.fixture
def client():
    from app.main import app

    # No `with` => skip lifespan (avoids vector-store/MCP startup); tables already created.
    return TestClient(app)


@pytest.fixture
def db():
    from app.database import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
