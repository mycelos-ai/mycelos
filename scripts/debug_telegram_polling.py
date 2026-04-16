#!/usr/bin/env python3
"""Debug: Test Telegram polling directly without the Gateway.

This starts polling in the MAIN thread (not daemon) so we see all errors.
Press Ctrl+C to stop.

Usage:
    python scripts/debug_telegram_polling.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("debug")

DATA_DIR = Path.home() / ".maicel"


def main():
    if not os.environ.get("MAICEL_MASTER_KEY"):
        key_file = DATA_DIR / ".master_key"
        if key_file.exists():
            os.environ["MAICEL_MASTER_KEY"] = key_file.read_text().strip()

    from maicel.app import App
    from maicel.chat.service import ChatService
    from maicel.channels.telegram import (
        dp, setup_telegram, load_channel_config,
    )

    app = App(DATA_DIR)

    # Load channel config
    channel_cfg = load_channel_config(app.storage)
    if not channel_cfg:
        logger.error("No Telegram channel config in DB. Run: maicel connector setup telegram")
        sys.exit(1)

    logger.info("Channel config: %s", channel_cfg)

    # Load bot token
    telegram_cred = app.credentials.get_credential("telegram")
    if not telegram_cred or not telegram_cred.get("api_key"):
        logger.error("No Telegram bot token. Run: maicel connector setup telegram")
        sys.exit(1)

    bot_token = telegram_cred["api_key"]
    allowed_users = channel_cfg.get("allowed_users", [])
    logger.info("Allowed users: %s", allowed_users)

    # Setup
    chat_service = ChatService(app)
    bot = setup_telegram(
        bot_token,
        chat_service,
        allowed_users=allowed_users,
    )

    logger.info("Bot initialized. Testing getMe...")

    # Test getMe
    async def test_bot():
        me = await bot.get_me()
        logger.info("Bot: @%s (%s)", me.username, me.first_name)
        return me

    loop = asyncio.new_event_loop()
    me = loop.run_until_complete(test_bot())
    logger.info("Bot verified: @%s", me.username)

    # Start polling in main thread (so we see all errors)
    logger.info("Starting polling... Send a message to @%s", me.username)
    logger.info("Press Ctrl+C to stop.\n")

    try:
        loop.run_until_complete(dp.start_polling(bot))
    except KeyboardInterrupt:
        logger.info("Stopped.")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
