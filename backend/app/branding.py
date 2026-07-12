"""Terminal branding and version discovery for startup."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.auth.service import BootstrapAdmin

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSION_PATH = PROJECT_ROOT / "VERSION"

_RESET = "\033[0m"
_BOLD = "\033[1m"
_MAGENTA = "\033[95m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_GREEN = "\033[92m"
_MUTED = "\033[38;5;146m"


def get_version(*, display: bool = True) -> str:
    """Read the repository's VERSION file, with a safe development fallback."""
    try:
        version = VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        version = "v0.0.0-dev"
    if not version:
        version = "v0.0.0-dev"
    return version if display else version.removeprefix("v")


def _paint(value: str, *codes: str, enabled: bool) -> str:
    return f"{''.join(codes)}{value}{_RESET}" if enabled else value


def render_startup_banner(
    bootstrap_admin: BootstrapAdmin | None = None,
    *,
    color: bool | None = None,
) -> str:
    """Build the startup banner; include the one-time credential only when just seeded."""
    if color is None:
        color = (
            os.environ.get("NO_COLOR") is None
            and (sys.stdout.isatty() or os.environ.get("PHLOX_FORCE_COLOR") == "1")
        )

    logo = [
        "             .-.             ",
        "        .---(   )---.         ",
        "       (      \\ /      )        ",
        "    --(--------o--------)--     ",
        "       (      /|\\      )        ",
        "        '---(   )---'         ",
        "             '-'             ",
    ]
    colored_logo = [
        _paint(line, _MAGENTA if i % 2 == 0 else _CYAN, _BOLD, enabled=color)
        for i, line in enumerate(logo)
    ]
    title = (
        f"{_paint('PHLOX', _MAGENTA, _BOLD, enabled=color)}  "
        f"{_paint(get_version(), _CYAN, _BOLD, enabled=color)}"
    )
    lines = [
        "",
        *colored_logo,
        title,
        _paint("Private AI workspace and agent harness", _MUTED, enabled=color),
    ]

    if bootstrap_admin is not None:
        rule = _paint("─" * 72, _CYAN, enabled=color)
        lines.extend(
            [
                rule,
                _paint("FIRST-RUN ADMINISTRATOR", _YELLOW, _BOLD, enabled=color),
                f"Username            {_paint(bootstrap_admin.username, _GREEN, enabled=color)}",
                "Temporary password  "
                + _paint(bootstrap_admin.password, _YELLOW, _BOLD, enabled=color),
                "Next step           Sign in and choose a new password before continuing.",
                _paint(
                    "This password is shown once. Store this first-run output securely.",
                    _MUTED,
                    enabled=color,
                ),
                rule,
            ]
        )

    return "\n".join(lines)


def emit_startup_banner(bootstrap_admin: BootstrapAdmin | None = None) -> None:
    """Print the startup banner, optionally bracketed for launch-script extraction."""
    markers = os.environ.get("PHLOX_STARTUP_CAPTURE_MARKERS") == "1"
    if markers:
        print("PHLOX_STARTUP_BEGIN")
    print(render_startup_banner(bootstrap_admin), flush=True)
    if markers:
        print("PHLOX_STARTUP_END", flush=True)
