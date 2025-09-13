import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import MenuButtonCommands, BotCommand
from aiogram.exceptions import TelegramUnauthorizedError, TelegramAPIError
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
    
    # Create bot instance
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode='HTML'))
    
    # Set bot commands (handle errors gracefully)
    try:
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
        logger.info("Bot commands set successfully")
    except TelegramUnauthorizedError:
        logger.warning("Invalid bot token. Skipping command setup. This is expected in development.")
    except TelegramAPIError as e:
        logger.warning(f"Telegram API error when setting commands: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error when setting bot commands: {e}")
    
    # Create dispatcher and start polling
    try:
        dp = Dispatcher()
        dp.include_router(root_router)
        logger.info("Routers registered, starting polling...")
        
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        logger.error("Invalid bot token. Please check your BOT_TOKEN in .env file.")
        logger.info("Bot shutting down due to invalid token.")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Bot crashed with error: {e}")