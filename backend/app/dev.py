"""Development server: source-only reloads and one signing secret per launcher lifetime."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def prepare_environment() -> None:
    # Set this before reading auth configuration. This entry point is explicitly dev-only;
    # production continues to use Uvicorn directly and its existing secret preflight.
    os.environ["PHLOX_ENV"] = "development"
    from app.config import get_auth_config

    # Inherit into reload children, without printing/persisting it or changing the caller's
    # shell. get_auth_config preserves an explicit environment or file secret when present.
    os.environ["PHLOX_JWT_SECRET"] = get_auth_config()["jwt_secret"]


def server_options(host: str, port: int, source: Path, data: Path) -> dict:
    return {
        "host": host,
        "port": port,
        "reload": True,
        "reload_dirs": [str(source.resolve())],
        # Also protect a custom data directory nested inside the source tree.
        "reload_excludes": [str(data.resolve())],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    prepare_environment()
    from app.config import DATA_DIR
    import uvicorn

    uvicorn.run("app.main:app", **server_options(args.host, args.port, Path(__file__).parent, DATA_DIR))


if __name__ == "__main__":
    main()
