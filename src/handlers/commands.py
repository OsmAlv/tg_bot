from aiogram import types
from aiogram.dispatcher import Dispatcher

async def start_command(message: types.Message):
    await message.answer("Welcome! Please send me a car listing link.")

async def help_command(message: types.Message):
    await message.answer("Send me a link to a car listing, and I'll help you with it.")

def register_commands(dp: Dispatcher):
    dp.register_message_handler(start_command, commands=["start"])
    dp.register_message_handler(help_command, commands=["help"])