import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import MenuButtonCommands, BotCommand
from app.config import settings
from app.handlers import router as root_router
from app.storage import ensure_bucket

# Enable logging
logging.basicConfig(level=logging.INFO)

async def main():
    logger = logging.getLogger(__name__)
    logger.info("Starting Telegram bot...")
    
    await ensure_bucket()
    logger.info("MinIO bucket ensured")
    
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode='HTML'))
    
    # Set bot commands
    await bot.set_my_commands([
        BotCommand(command="start", description="Start the bot"),
        BotCommand(command="project", description="Select/create project"),
        BotCommand(command="memory", description="Manage memory entries"),
        BotCommand(command="import", description="Import files"),
        BotCommand(command="ask", description="Ask questions with context"),
        BotCommand(command="ctx", description="Set context filters"),
        BotCommand(command="model", description="Select AI model"),
        BotCommand(command="menu", description="Open quick actions menu"),
        BotCommand(command="actions", description="Open advanced actions panel"),
        BotCommand(command="status", description="Show current status"),
        BotCommand(command="kb_on", description="Enable keyboard"),
    ])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    
    dp = Dispatcher()
    dp.include_router(root_router)
    logger.info("Routers registered, starting polling...")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())