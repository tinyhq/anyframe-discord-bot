from __future__ import annotations

from typing import Any


def render_block(block: dict[str, Any]) -> str | None:
    kind = (block.get("type") or block.get("__class__", "")).lower()
    if kind in ("text", "textblock"):
        text = (block.get("text") or "").strip()
        return text or None
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
    if t == "permission_request":
        return (
            f"⚠️ permission needed for `{payload.get('tool_name')}` — "
            f"open the dashboard to approve."
        )
    if t == "ask_user_question":
        return f"❓ {payload.get('question')}"
    if t == "_error":
        return f"❌ {payload.get('error') or 'unknown error'}"
    # `result` is just a turn-end marker; caller uses it to stop streaming.
    return None
