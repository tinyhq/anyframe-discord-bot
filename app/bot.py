"""Discord bot that drives an anyframe agent.

UX
--
* First @mention of the bot in a channel spawns a new thread and a new
  anyframe session. The mention text becomes the opening prompt.
* Subsequent messages in that thread are forwarded to the same session —
  no mention needed. The mapping ``thread_id -> (session_id, last_seq)``
  lives in SQLite on a persistent volume so it survives bot restarts.
* If the session has terminated/evicted between replies, the bot calls
  ``resume`` which boots a fresh sandbox from the latest snapshot.
* Streamed events from the session are rendered into the thread as they
  arrive.
"""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any

import discord
from anyframe.exceptions import APIError

from . import events, sessions, state
from .config import settings
from .sessions import BootFailed

logger = logging.getLogger("anyframe-discord-bot")


async def _stream_turn(sid: str, thread: discord.Thread, since_seq: int) -> int:
    """Stream a single turn into the thread. Returns the highest seq seen."""
    buf = ""
    last_seq = since_seq

    async def flush(force: bool = False) -> None:
        nonlocal buf
        if not buf:
            return
        if force or len(buf) >= settings.discord_msg_limit:
            chunk, buf = buf[: settings.discord_msg_limit], buf[settings.discord_msg_limit :]
            await thread.send(chunk)

    stream = sessions.client().sessions.events(
        sid,
        last_event_id=str(since_seq) if since_seq > 0 else None,
    )
    async for event in stream:
        if not event.data:
            continue
        try:
            payload: dict[str, Any] = event.json()
        except json.JSONDecodeError:
            continue
        if event.id is not None:
            try:
                last_seq = max(last_seq, int(event.id))
            except ValueError:
                pass
        rendered = events.render_event(payload)
        if rendered:
            buf += rendered + "\n\n"
            await flush()
        if payload.get("type") == "result":
            await flush(force=True)
            return last_seq
    await flush(force=True)
    return last_seq


def make_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        logger.info("logged in as %s (%s)", client.user, client.user.id if client.user else "?")

    @client.event
    async def on_message(msg: discord.Message) -> None:
        logger.debug(
            "on_message author=%s content=%r mentions=%s channel=%s",
            msg.author,
            msg.content,
            [m.id for m in msg.mentions],
            type(msg.channel).__name__,
        )
        try:
            await _handle_message(client, msg)
        except Exception:
            traceback.print_exc()

    async def _handle_message(client: discord.Client, msg: discord.Message) -> None:
        if msg.author.bot or client.user is None:
            return

        is_thread = isinstance(msg.channel, discord.Thread)
        me = msg.guild.me if msg.guild else None
        mentioned = client.user in msg.mentions or (
            me is not None and any(r in msg.role_mentions for r in me.roles)
        )

        thread_id = str(msg.channel.id)
        existing = state.load(thread_id) if is_thread else None

        if is_thread and existing:
            thread = msg.channel
            prompt = msg.content.replace(f"<@{client.user.id}>", "").strip()
            if not prompt:
                return
        elif mentioned and not is_thread:
            prompt = msg.content.replace(f"<@{client.user.id}>", "").strip()
            if not prompt:
                await msg.reply("Tell me what to bug-bash and I'll spin up a sandbox.")
                return
            thread = await msg.create_thread(
                name=prompt[: settings.thread_name_limit] or "bugbash",
                auto_archive_duration=settings.thread_auto_archive_minutes,
            )
        else:
            return

        try:
            sid = await sessions.ensure_session(str(thread.id))
        except BootFailed as e:
            await thread.send(f"❌ {e}")
            return

        try:
            await sessions.client().sessions.message(sid, {"prompt": prompt})
        except APIError as e:
            await thread.send(f"❌ failed to send message: {e}")
            return

        cur = state.load(str(thread.id))
        last_seq = cur[1] if cur else 0
        try:
            new_seq = await _stream_turn(sid, thread, last_seq)
            state.update_seq(str(thread.id), new_seq)
        except APIError as e:
            await thread.send(f"⚠️ stream interrupted: {e}")

    return client
