from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from anyframe import AsyncAnyFrame
from anyframe.exceptions import AnyFrameError, NotFoundError

from . import state
from .config import settings

logger = logging.getLogger("anyframe-discord-bot")

_thread_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

_client: AsyncAnyFrame | None = None


def client() -> AsyncAnyFrame:
    global _client
    if _client is None:
        _client = AsyncAnyFrame(
            api_key=settings.anyframe_api_key,
            base_url=settings.anyframe_base_url,
            load_dotenv=False,
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class BootFailed(Exception):
    """The session could not be started or resumed."""


async def _boot_fresh(thread_id: str) -> str:
    af = client()
    try:
        sess = await af.sessions.create(agent_id=settings.anyframe_agent_id)
    except AnyFrameError as e:
        logger.warning("session create failed: %s", e)
        raise BootFailed(f"session create failed: {e}") from e
    sid = str(sess.id)
    state.save(thread_id, sid, 0)
    try:
        await af.sessions.wait_until_running(sid, timeout=settings.boot_timeout_s)
    except (AnyFrameError, TimeoutError) as e:
        logger.warning("boot failed for thread=%s sid=%s: %s", thread_id, sid, e)
        state.delete(thread_id)
        raise BootFailed(f"session boot failed: {e}") from e
    return sid


async def ensure_session(thread_id: str) -> str:
    """Return a running session_id for this thread. Create or resume as needed."""
    async with _thread_locks[thread_id]:
        cur = state.load(thread_id)
        if cur is None:
            return await _boot_fresh(thread_id)

        sid, _seq = cur
        af = client()
        try:
            sess = await af.sessions.get(sid)
        except NotFoundError:
            state.delete(thread_id)
            return await _boot_fresh(thread_id)

        if sess.status == "running":
            return sid

        if sess.status == "terminated":
            try:
                await af.sessions.resume(sid)
            except AnyFrameError as e:
                logger.info("resume failed (%s); booting fresh", e)
                state.delete(thread_id)
                return await _boot_fresh(thread_id)
            try:
                await af.sessions.wait_until_running(sid, timeout=settings.boot_timeout_s)
            except (AnyFrameError, TimeoutError):
                state.delete(thread_id)
                return await _boot_fresh(thread_id)
            return sid

        # error / unexpected state — start over
        state.delete(thread_id)
        return await _boot_fresh(thread_id)
