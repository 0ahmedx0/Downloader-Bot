import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums.parse_mode import ParseMode
from aiohttp import ClientSession

from config import BOT_TOKEN, BOT_COMMANDS, OUTPUT_DIR, custom_api_url, MEASUREMENT_ID, API_SECRET
from services.db import DataBase

logging.basicConfig(level=logging.INFO)

custom_timeout = 600  # 10 minutes

session = AiohttpSession(
    api=TelegramAPIServer.from_base(custom_api_url),
    timeout=custom_timeout
)

default = DefaultBotProperties(parse_mode=ParseMode.HTML)
bot = Bot(token=BOT_TOKEN, default=default, session=session)

dp = Dispatcher()

db = DataBase()

os.makedirs("downloads", exist_ok=True)


async def send_analytics(user_id, chat_type, action_name):
    """
    Send record to Google Analytics
    """
    params = {
        'client_id': str(user_id),
        'user_id': str(user_id),
        'events': [{
            'name': action_name,
            'params': {
                'chat_type': chat_type,
                "session_id": str(user_id),
                'engagement_time_msec': '1'
            }
        }],
    }
    print(params)
    async with ClientSession() as client:
        await client.post(
            f"https://www.google-analytics.com/mp/collect?measurement_id={MEASUREMENT_ID}&api_secret={API_SECRET}",
            json=params)


async def main():
    import handlers
    import middlewares

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    dp.include_router(handlers.router)
    for middleware in middlewares.__all__:
        dp.message.middleware(middleware())
        dp.callback_query.middleware(middleware())
    await bot.set_my_commands(commands=BOT_COMMANDS)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
