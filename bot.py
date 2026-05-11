"""Discord bot that drives an anyframe agent.

UX
--
* First @mention of the bot in a channel spawns a new thread and a new
  anyframe session. The mention text becomes the opening prompt.
* Subsequent messages in that thread are forwarded to the same session —
  no mention needed. The mapping ``discord_thread_id → (session_id,
  last_seq)`` lives in a tiny SQLite file on a persistent volume so it
  survives bot restarts.
* If the session has terminated/evicted between replies, the bot calls
  ``/resume`` which boots a fresh sandbox from the latest snapshot.
* Streamed events from the session are rendered into the thread as they
  arrive.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

import discord
import httpx

BASE_URL = os.environ["ANYFRAME_BASE_URL"].rstrip("/")
API_TOKEN = os.environ["ANYFRAME_API_TOKEN"]
AGENT_ID = int(os.environ["ANYFRAME_AGENT_ID"])
DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
STATE_DB_PATH = os.environ.get("STATE_DB_PATH", "/data/state.db")

DISCORD_MSG_LIMIT = 1900  # 2000 hard cap; leave headroom for code fences
BOOT_POLL_INTERVAL = 2.0
BOOT_TIMEOUT = 180.0

# In-process per-thread locks so rapid replies serialise.
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _init_db() -> sqlite3.Connection:
    Path(STATE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DB_PATH, check_same_thread=False, isolation_level=None)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS thread_sessions ("
        "thread_id TEXT PRIMARY KEY, "
        "session_id TEXT NOT NULL, "
        "last_seq INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_db = _init_db()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_TOKEN}"}


def _load_state(thread_key: str) -> tuple[str | None, int]:
    row = _db.execute(
        "SELECT session_id, last_seq FROM thread_sessions WHERE thread_id = ?",
        (thread_key,),
    ).fetchone()
    if row is None:
        return None, 0
    return row[0], int(row[1])


def _save_state(thread_key: str, sid: str, last_seq: int) -> None:
    _db.execute(
        "INSERT INTO thread_sessions(thread_id, session_id, last_seq) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(thread_id) DO UPDATE SET "
        "session_id=excluded.session_id, last_seq=excluded.last_seq",
        (thread_key, sid, last_seq),
    )


# ── anyframe HTTP helpers ───────────────────────────────────────────────────


async def api_get(http: httpx.AsyncClient, path: str) -> Any:
    r = await http.get(f"{BASE_URL}{path}", headers=_auth_headers())
    r.raise_for_status()
    return r.json()


async def api_post(
    http: httpx.AsyncClient, path: str, body: dict | None = None,
) -> Any:
    r = await http.post(
        f"{BASE_URL}{path}", headers=_auth_headers(), json=body or {},
    )
    r.raise_for_status()
    return r.json() if r.content else None


async def create_session(http: httpx.AsyncClient) -> str:
    s = await api_post(http, "/api/sessions", {"agent_id": AGENT_ID})
    return s["id"]


async def wait_until_running(
    http: httpx.AsyncClient, sid: str, thread: discord.Thread,
) -> bool:
    """Block until the session is running. Returns False on terminal failure."""
    elapsed = 0.0
    while elapsed < BOOT_TIMEOUT:
        s = await api_get(http, f"/api/sessions/{sid}")
        st = s["status"]
        if st == "running":
            return True
        if st == "error":
            await thread.send(f"❌ session entered `error` state")
            return False
        await asyncio.sleep(BOOT_POLL_INTERVAL)
        elapsed += BOOT_POLL_INTERVAL
    await thread.send(f"⏱️ session didn't reach running state in {BOOT_TIMEOUT:.0f}s")
    return False


async def ensure_running(
    http: httpx.AsyncClient, sid: str, thread: discord.Thread,
) -> bool:
    """Make sure the session is running, resuming from snapshot if needed."""
    try:
        s = await api_get(http, f"/api/sessions/{sid}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return False  # session was hard-deleted upstream
        raise

    st = s["status"]
    if st == "running":
        return True
    if st in ("terminated",):
        await thread.send("💤 resuming from snapshot…")
        try:
            await api_post(http, f"/api/sessions/{sid}/resume", {})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                # "no snapshot to resume from" — caller should start fresh
                return False
            raise
    return await wait_until_running(http, sid, thread)


# ── event rendering ─────────────────────────────────────────────────────────


def _render_block(block: dict) -> str | None:
    kind = block.get("type") or block.get("__class__", "").lower()
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
    if kind in ("thinking", "thinkingblock"):
        return None  # too noisy for Discord
    return None


def render_event(payload: dict) -> str | None:
    t = payload.get("type")
    if t == "assistant":
        blocks = (payload.get("message") or {}).get("content") or payload.get("content") or []
        chunks = [c for b in blocks if (c := _render_block(b))]
        return "\n".join(chunks) if chunks else None
    if t == "result":
        return None
    if t == "permission_request":
        return (
            f"⚠️ permission needed for `{payload.get('tool_name')}` — "
            f"open the dashboard to approve."
        )
    if t == "ask_user_question":
        return f"❓ {payload.get('question')}"
    if t == "_error":
        return f"❌ {payload.get('error') or 'unknown error'}"
    return None


# ── streaming ───────────────────────────────────────────────────────────────


async def stream_session(
    http: httpx.AsyncClient, sid: str, thread: discord.Thread, since_seq: int,
) -> int:
    """Stream a single turn. Returns the highest seq seen so the caller can
    persist it and pass it back as Last-Event-ID on the next turn."""
    buf = ""
    last_seq = since_seq

    async def flush(force: bool = False) -> None:
        nonlocal buf
        if not buf:
            return
        if force or len(buf) >= DISCORD_MSG_LIMIT:
            chunk, buf = buf[:DISCORD_MSG_LIMIT], buf[DISCORD_MSG_LIMIT:]
            await thread.send(chunk)

    headers = {**_auth_headers(), "Accept": "text/event-stream"}
    if since_seq > 0:
        headers["Last-Event-ID"] = str(since_seq)

    async with http.stream(
        "GET", f"{BASE_URL}/api/sessions/{sid}/events", headers=headers,
    ) as r:
        r.raise_for_status()
        cur_id: int | None = None
        data_lines: list[str] = []
        async for line in r.aiter_lines():
            if line == "":
                if data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        data_lines = []
                        cur_id = None
                        continue
                    if cur_id is not None:
                        last_seq = max(last_seq, cur_id)
                    data_lines = []
                    rendered = render_event(payload)
                    if rendered:
                        buf += rendered + "\n\n"
                        await flush()
                    if payload.get("type") == "result":
                        await flush(force=True)
                        return last_seq
                cur_id = None
                continue
            if line.startswith("id:"):
                try:
                    cur_id = int(line[3:].strip())
                except ValueError:
                    cur_id = None
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
    return last_seq


# ── Discord wiring ──────────────────────────────────────────────────────────


def make_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        print(f"logged in as {client.user} ({client.user.id})")

    @client.event
    async def on_message(msg: discord.Message) -> None:
        if msg.author.bot or client.user is None:
            return

        is_thread = isinstance(msg.channel, discord.Thread)
        mentioned = client.user in msg.mentions

        thread_key = str(msg.channel.id)
        existing_sid, existing_seq = _load_state(thread_key) if is_thread else (None, 0)

        if is_thread and existing_sid:
            thread = msg.channel
            sid, last_seq = existing_sid, existing_seq
            prompt = msg.content.replace(f"<@{client.user.id}>", "").strip()
            if not prompt:
                return
        elif mentioned and not is_thread:
            prompt = msg.content.replace(f"<@{client.user.id}>", "").strip()
            if not prompt:
                await msg.reply("Tell me what to bug-bash and I'll spin up a sandbox.")
                return
            thread = await msg.create_thread(
                name=prompt[:80] or "bugbash",
                auto_archive_duration=10080,  # 7 days
            )
            sid, last_seq = None, 0
        else:
            return

        async with _locks[thread.id]:
            async with httpx.AsyncClient(timeout=None) as http:
                if sid is None:
                    sid = await create_session(http)
                    last_seq = 0
                    _save_state(str(thread.id), sid, last_seq)
                    if not await wait_until_running(http, sid, thread):
                        return
                else:
                    ok = await ensure_running(http, sid, thread)
                    if not ok:
                        # snapshot gone or session vanished — start over.
                        await thread.send("ℹ️ couldn't resume; starting a fresh sandbox.")
                        sid = await create_session(http)
                        last_seq = 0
                        _save_state(str(thread.id), sid, last_seq)
                        if not await wait_until_running(http, sid, thread):
                            return

                try:
                    await api_post(
                        http, f"/api/sessions/{sid}/message", {"prompt": prompt},
                    )
                except httpx.HTTPStatusError as e:
                    await thread.send(f"❌ failed to send message: {e.response.status_code}")
                    return

                try:
                    new_seq = await stream_session(http, sid, thread, last_seq)
                    _save_state(str(thread.id), sid, new_seq)
                except httpx.HTTPError as e:
                    await thread.send(f"⚠️ stream interrupted: {e}")

    return client


def main() -> None:
    client = make_client()
    client.run(DISCORD_TOKEN)
