"""Operator-friendly checks used by the local production start scripts.

The application repeats these checks during its lifespan startup. This small CLI exists
so the convenience scripts can fail before building the frontend or launching Uvicorn,
without making operators dig through a traceback in the backend log.
"""
from __future__ import annotations

import argparse
import sys

from app.config import validate_auth_startup


def main(shell: str = "posix") -> int:
    try:
        validate_auth_startup()
    except RuntimeError as exc:
        print("", file=sys.stderr)
        print(f"Production security preflight failed: {exc}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Generate a production-ready secret (32 random bytes):", file=sys.stderr)
        print(
            "  uv run --directory backend python -c "
            "'import secrets; print(secrets.token_hex(32))'",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print("Then set the generated value and start Phlox again:", file=sys.stderr)
        if shell == "powershell":
            print("  $env:PHLOX_JWT_SECRET = '<generated-value>'", file=sys.stderr)
            print(r"  .\scripts\start.ps1 prod", file=sys.stderr)
        else:
            print("  export PHLOX_JWT_SECRET='<generated-value>'", file=sys.stderr)
            print("  ./scripts/start.sh prod", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "Store this secret in a password/secret manager and reuse the same value on "
            "every restart. Changing it signs out every user.",
            file=sys.stderr,
        )
        print("See docs/AUTH.md for deployment and rotation guidance.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check Phlox production startup settings.")
    parser.add_argument(
        "--powershell",
        action="store_true",
        help="format remediation commands for PowerShell",
    )
    args = parser.parse_args()
    selected_shell = "powershell" if args.powershell else "posix"
    raise SystemExit(main(selected_shell))
