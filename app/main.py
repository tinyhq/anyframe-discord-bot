from __future__ import annotations

import asyncio
import logging

from . import bot, sessions
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def _run() -> None:
    client = bot.make_client()
    try:
        await client.start(settings.discord_bot_token)
    finally:
        await client.close()
        await sessions.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
