from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot

from services.currency_service import CurrencyService
from services.market_watcher import (
    load_seen_urls,
    load_watch_presets,
    run_market_watch,
    save_seen_urls,
)
from services.price_calculator import PriceCalculator
from utils.helpers import load_settings

logger = logging.getLogger(__name__)


def _default_state_path() -> str:
    volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if volume_mount:
        return str(Path(volume_mount) / "autopost_seen.json")

    if Path("/data").exists():
        return "/data/autopost_seen.json"

    return "data/autopost_seen.json"


async def run_once() -> None:
    settings = load_settings()
    if not settings.autopost_channel:
        raise ValueError("AUTOPOST_CHANNEL is required for auto scanner")

    config_path = os.getenv("AUTO_SCAN_CONFIG_PATH", "autopost_filters.json")
    state_path = os.getenv("AUTO_SCAN_STATE_PATH", _default_state_path())

    presets = load_watch_presets(config_path)
    seen_urls = load_seen_urls(state_path)

    currency_service = CurrencyService(
        timeout_seconds=settings.http_timeout_seconds,
        krw_per_usd=settings.krw_per_usd,
        fixed_usd_uzs=settings.fixed_usd_uzs,
    )
    price_calculator = PriceCalculator()
    bot = Bot(token=settings.telegram_bot_token)

    try:
        result = await run_market_watch(
            bot=bot,
            channel_id=settings.autopost_channel,
            manager_chat_url=settings.manager_chat_url,
            currency_service=currency_service,
            price_calculator=price_calculator,
            presets=presets,
            seen_urls=seen_urls,
        )
        save_seen_urls(state_path, seen_urls)
        logger.info(
            "Auto-scan finished: checked=%d matched=%d posted=%d",
            result.checked,
            result.matched,
            result.posted,
        )
    finally:
        await bot.session.close()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    interval_minutes_raw = os.getenv("AUTO_SCAN_INTERVAL_MINUTES", "").strip()
    if not interval_minutes_raw:
        await run_once()
        return

    interval_minutes = max(1, int(interval_minutes_raw))
    logger.info("Starting auto-scan loop, interval=%d minutes", interval_minutes)

    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("Auto-scan run failed")
        await asyncio.sleep(interval_minutes * 60)


if __name__ == "__main__":
    asyncio.run(main())
