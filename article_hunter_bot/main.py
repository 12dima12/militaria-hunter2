import asyncio
import logging
import os
from contextlib import asynccontextmanager

from database import DatabaseManager
from bot.telegram_bot import TelegramBotManager
from scheduler import PollingScheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def main():
    """Main application entry point"""
    # Initialize database
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    # Initialize bot manager
    bot_manager = TelegramBotManager(db_manager)
    await bot_manager.initialize()
    
    # Initialize scheduler
    scheduler = PollingScheduler(
        db_manager,
        bot_manager.search_service,
        bot_manager.notification_service
    )
    
    try:
        # Start scheduler
        await scheduler.start()
        
        # Start bot (this will block)
        logger.info("Starting Article Hunter Bot...")
        await bot_manager.start()
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Application error: {e}")
    finally:
        # Cleanup
        logger.info("Shutting down...")
        await scheduler.stop()
        await bot_manager.stop()
        await db_manager.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped")
