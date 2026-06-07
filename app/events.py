from __future__ import annotations

import json
import re
from typing import Any

# Bare URLs Discord auto-embeds (images/video render inline; others link).
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
_MEDIA_HINTS = (
    "r2.cloudflarestorage", "amazonaws", "/shared/",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".pdf",
)


def _media_urls(text: str | None) -> list[str]:
    if not text:
        return []
    out: dict[str, None] = {}
    for u in _URL_RE.findall(text):
        if any(h in u for h in _MEDIA_HINTS):
            out.setdefault(u, None)
    return list(out)


def render_block(block: dict[str, Any]) -> str | None:
    kind = (block.get("type") or block.get("__class__", "")).lower()
    if kind in ("text", "textblock"):
        return (block.get("text") or "").strip() or None
    if kind in ("image", "imageblock"):
        src = block.get("url")
        if not src and isinstance(block.get("source"), dict):
            src = block["source"].get("url")
        return src or None
    if kind in ("tool_use", "tooluseblock"):
        name = block.get("name", "?")
        inp = block.get("input") or {}
        summary = next(
            (str(v) for k, v in inp.items() if k in ("command", "file_path", "path", "query")),
            "",
        )
        summary = summary[:140]
        return f"🔧 `{name}`" + (f" — `{summary}`" if summary else "")
    # thinking blocks are filtered out elsewhere — too noisy for Discord
    return None


def _tool_result_media(blocks: list[Any]) -> list[str]:
    """Shared file/image URLs from tool-result blocks (e.g. share_file output)."""
    urls: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        content = b.get("content")
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        urls += _media_urls(text)
    return list(dict.fromkeys(urls))


def render_event(payload: dict[str, Any]) -> str | None:
    t = payload.get("type")
    if t == "assistant":
        blocks = (payload.get("message") or {}).get("content") or payload.get("content") or []
        if payload.get("delta"):
            # Streaming fragment — concatenate text as-is, no stripping or joining
            text = "".join(
                b.get("text") or ""
                for b in blocks
                if (b.get("type") or b.get("__class__", "")).lower() in ("text", "textblock")
            )
            return text or None
        chunks = [c for b in blocks if (c := render_block(b))]
        return "\n".join(chunks) if chunks else None
    if t == "user":
        # tool results — surface shared file/image URLs (share_file etc.).
        # Discord auto-embeds image/video links inline; quiet otherwise.
        blocks = (payload.get("message") or {}).get("content") or payload.get("content") or []
        urls = _tool_result_media(blocks)
        return "\n".join(urls) if urls else None
    if t == "permission_request":
        return (
            f"⚠️ permission needed for `{payload.get('tool_name')}` — "
            f"open the dashboard to approve."
        )
    if t == "ask_user_question":
        return f"❓ {payload.get('question')}"
    if t == "_warn":
        return f"⚠️ {payload.get('message') or 'warning'}"
    if t == "_error":
        return f"❌ {payload.get('error') or 'unknown error'}"
    # `result` is just a turn-end marker; caller uses it to stop streaming.
    return None
