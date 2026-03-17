from aiogram import types
from aiogram.dispatcher import Dispatcher

async def handle_callback_query(callback_query: types.CallbackQuery):
    # This function will handle callback queries from inline buttons
    await callback_query.answer("This feature is not implemented yet.") 

def register_callbacks(dp: Dispatcher):
    dp.register_callback_query_handler(handle_callback_query)