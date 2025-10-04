import logging
from datetime import datetime
from typing import Optional
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

from models import User, Keyword, StoredListing, Notification
from database import DatabaseManager

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending Telegram notifications"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.bot: Optional[Bot] = None
        self._initialize_bot()
    
    def _initialize_bot(self):
        """Initialize Telegram bot"""
        token = os.environ.get('TELEGRAM_BOT_TOKEN')
        if token:
            self.bot = Bot(token=token)
            logger.info("Telegram bot initialized for notifications")
        else:
            logger.error("TELEGRAM_BOT_TOKEN not found in environment")
    
    async def send_listing_notification(self, user: User, keyword: Keyword, listing: StoredListing) -> bool:
        """Send notification about new listing"""
        if not self.bot:
            logger.error("Bot not initialized, cannot send notification")
            return False
        
        try:
            # Format price using German locale
            price_text = ""
            if listing.price_value and listing.price_currency:
                from decimal import Decimal
                from providers.militaria321 import Militaria321Provider
                
                provider = Militaria321Provider()
                formatted_price = provider.format_price_de(Decimal(str(listing.price_value)), listing.price_currency)
                price_text = f"\n💰 **{formatted_price}**"
            elif listing.price_value:
                from decimal import Decimal
                from providers.militaria321 import Militaria321Provider
                
                provider = Militaria321Provider()
                formatted_price = provider.format_price_de(Decimal(str(listing.price_value)), "EUR")
                price_text = f"\n💰 **{formatted_price}**"
            
            # Format location
            location_text = ""
            if listing.location:
                location_text = f"\n📍 {listing.location}"
            
            # Format condition
            condition_text = ""
            if listing.condition:
                condition_text = f"\n🏷️ Zustand: {listing.condition}"
            
            # Format seller
            seller_text = ""
            if listing.seller_name:
                seller_text = f"\n👤 Verkäufer: {listing.seller_name}"
            
            # Create message
            message_text = f"""🎖️ **Neuer Treffer gefunden!**

🔍 **Suchbegriff:** {keyword.keyword}
📝 **Titel:** {listing.title}{price_text}{location_text}{condition_text}{seller_text}

🌐 **Plattform:** {listing.platform}
🕐 **Gefunden:** {listing.first_seen_ts.strftime('%d.%m. %H:%M')}"""
            
            # Create inline keyboard
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔗 Öffnen", url=listing.url),
                    InlineKeyboardButton(text="✅ Gesehen", callback_data=f"mark_seen_{listing.id}")
                ],
                [
                    InlineKeyboardButton(text="🔇 Stumm 30m", callback_data=f"mute_30m_{keyword.id}"),
                    InlineKeyboardButton(text="🗑️ Keyword löschen", callback_data=f"delete_{keyword.id}")
                ]
            ])
            
            # Send message
            message = await self.bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            
            # Record notification
            notification = Notification(
                user_id=user.id,
                keyword_id=keyword.id,
                listing_id=listing.id,
                telegram_message_id=message.message_id,
                status="sent"
            )
            await self.db.create_notification(notification)
            
            logger.info(f"Sent notification to user {user.telegram_id} for listing {listing.title}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending notification: {e}")
            
            # Record failed notification
            notification = Notification(
                user_id=user.id,
                keyword_id=keyword.id,
                listing_id=listing.id,
                status="failed"
            )
            await self.db.create_notification(notification)
            
            return False
    
    async def send_system_message(self, telegram_id: int, message: str) -> bool:
        """Send system message to user"""
        if not self.bot:
            return False
        
        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="Markdown"
            )
            return True
        except Exception as e:
            logger.error(f"Error sending system message: {e}")
            return False