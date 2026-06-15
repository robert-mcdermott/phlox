"""Storage for user-attached images on chat messages.

Images arrive as base64 data URLs in the chat request. We persist them to disk
(``data/attachments/<message_id>/<idx>.<ext>``) and store lightweight refs on the
``Message`` (so the DB stays small). When rebuilding history for a vision model, the
files are re-read and inlined as data URLs.
"""
from __future__ import annotations

import base64
import re

from app.config import ATTACHMENTS_DIR

_DATA_URL = re.compile(r"data:(?P<mime>image/(?P<fmt>\w+));base64,(?P<b64>.+)", re.DOTALL)


def save_message_images(message_id: str, data_urls: list[str]) -> list[dict]:
    """Persist images; return attachment refs [{type, idx, mime, url}]."""
    refs: list[dict] = []
    msg_dir = ATTACHMENTS_DIR / message_id
    for idx, url in enumerate(data_urls):
        m = _DATA_URL.match(url)
        if not m:
            continue
        try:
            raw = base64.b64decode(m.group("b64"))
        except Exception:  # noqa: BLE001
            continue
        msg_dir.mkdir(parents=True, exist_ok=True)
        ext = {"jpeg": "jpg"}.get(m.group("fmt").lower(), m.group("fmt").lower())
        (msg_dir / f"{idx}.{ext}").write_bytes(raw)
        refs.append(
            {
                "type": "image",
                "idx": idx,
                "mime": m.group("mime"),
                "ext": ext,
                "url": f"/api/attachments/{message_id}/{idx}",
            }
        )
    return refs


def load_image_data_urls(message_id: str, attachments: list[dict]) -> list[str]:
    """Re-read attachment images from disk as base64 data URLs (for the provider)."""
    urls: list[str] = []
    for ref in attachments or []:
        if ref.get("type") != "image":
            continue
        path = ATTACHMENTS_DIR / message_id / f"{ref['idx']}.{ref.get('ext', 'png')}"
        if not path.is_file():
            continue
        b64 = base64.b64encode(path.read_bytes()).decode()
        urls.append(f"data:{ref.get('mime', 'image/png')};base64,{b64}")
    return urls
