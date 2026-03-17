from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils import executor
import logging
import os
from utils.config import load_config
from handlers.commands import register_commands
from handlers.filters import register_filters
from handlers.callbacks import register_callbacks

logging.basicConfig(level=logging.INFO)

config = load_config()
bot = Bot(token=config.TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

async def on_startup(dp):
    register_commands(dp)
    register_filters(dp)
    register_callbacks(dp)
    logging.info("Bot is online!")

if __name__ == '__main__':
    executor.start_polling(dp, on_startup=on_startup)