import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from database import DatabaseManager
from bot.handlers import router
from bot.callbacks import callback_router

logger = logging.getLogger(__name__)


class TelegramBotManager:
    """Telegram bot manager"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.bot = None
        self.dp = None
        self.is_running = False
        self._initialize()
    
    def _initialize(self):
        """Initialize bot and dispatcher"""
        token = os.environ.get('TELEGRAM_BOT_TOKEN')
        if not token:
            logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
            return
        
        # Create bot instance
        self.bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
        )
        
        # Create dispatcher
        self.dp = Dispatcher()
        
        # Register routers
        self.dp.include_router(router)
        self.dp.include_router(callback_router)
        
        logger.info("Telegram bot initialized")
    
    async def start(self):
        """Start the bot"""
        if not self.bot:
            logger.error("Bot not initialized")
            return
        
        if self.is_running:
            logger.warning("Bot already running")
            return
        
        try:
            # Initialize database
            await self.db.initialize()
            
            # Start polling
            self.is_running = True
            
            logger.info("Starting Telegram bot...")
            await self.dp.start_polling(self.bot)
            
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            self.is_running = False
            raise
    
    async def stop(self):
        """Stop the bot"""
        if not self.is_running:
            return
        
        try:
            logger.info("Stopping Telegram bot...")
            
            # Stop polling
            await self.dp.stop_polling()
            
            # Close bot session
            if self.bot:
                await self.bot.session.close()
            
            self.is_running = False
            logger.info("Telegram bot stopped")
            
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")
    
    async def get_bot_info(self):
        """Get bot information"""
        if not self.bot:
            return None
        
        try:
            me = await self.bot.get_me()
            return {
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name,
                "is_bot": me.is_bot
            }
        except Exception as e:
            logger.error(f"Error getting bot info: {e}")
            return None


# Global bot manager instance
telegram_bot_manager = None