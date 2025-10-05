import logging
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import DatabaseManager
from models import Keyword, Listing, Notification
from providers.militaria321 import Militaria321Provider
from utils.text import br_join, b, i, a, code, fmt_ts_de, fmt_price_de, safe_truncate

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending German-formatted notifications"""
    
    def __init__(self, db_manager: DatabaseManager, bot: Bot):
        self.db = db_manager
        self.bot = bot
        self.militaria321_provider = Militaria321Provider()
    
    async def send_new_item_notification(
        self, 
        user_telegram_id: int, 
        keyword: Keyword, 
        item: Listing
    ) -> bool:
        """Send notification for new item with German formatting
        
        Returns True if notification was sent (passed idempotency check)
        """
        # Build canonical listing key
        listing_key = self._build_canonical_listing_key(item)
        
        # Create notification record for idempotency
        notification = Notification(
            user_id=keyword.user_id,
            keyword_id=keyword.id,
            listing_key=listing_key
        )
        
        # Try to insert notification (idempotency check)
        is_new = await self.db.create_notification(notification)
        if not is_new:
            logger.info({
                "event": "decision",
                "platform": item.platform,
                "listing_key": listing_key,
                "decision": "notif_duplicate",
                "reason": "notification_already_sent"
            })
            return False
        
        try:
            # Format notification message in German
            message_text = self._format_notification_message(keyword, item)
            
            # Create inline keyboard with only specified buttons
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Öffnen", url=item.url),
                    InlineKeyboardButton(text="Keyword löschen", callback_data=f"delete_keyword_{keyword.id}")
                ]
            ])
            
            # Send notification
            await self.bot.send_message(
                chat_id=user_telegram_id,
                text=message_text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=False  # Show preview for image_url
            )
            
            # Log message with preview
            logger.info({"event": "send_text", "len": len(message_text), "preview": message_text[:120].replace("\n", "⏎")})
            logger.info(f"Notification sent for {listing_key} to user {user_telegram_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send notification for {listing_key}: {e}")
            return False
    
    def _format_notification_message(self, keyword: Keyword, item: Listing) -> str:
        """Format notification message with German texts and Berlin timezone
        
        Template as specified in requirements
        """
        # Format price and timestamps using utilities
        preis = fmt_price_de(item.price_value, item.price_currency)
        gefunden = fmt_ts_de(datetime.now(ZoneInfo('UTC')))
        inseriert_am = fmt_ts_de(item.posted_ts)
        
        # Build message with proper HTML formatting
        return br_join([
            f"🔎 {b('Neues Angebot gefunden')}",
            "",
            f"Suchbegriff: {keyword.original_keyword}",
            f"Titel: {safe_truncate(item.title, 80)}",
            f"Preis: {preis}",
            f"Plattform: {a('militaria321.com', item.url)}",
            f"Gefunden: {gefunden}",
            f"Eingestellt am: {inseriert_am}"
        ])
    
    def _build_canonical_listing_key(self, item: Listing) -> str:
        """Build canonical listing key: militaria321.com:<numeric_id>"""
        # Ensure platform is lowercase and normalized
        platform = item.platform.lower().strip()
        
        # Extract numeric ID if platform_id contains extra data
        numeric_id = re.search(r'(\d+)', item.platform_id)
        if numeric_id:
            clean_id = numeric_id.group(1)
        else:
            clean_id = item.platform_id
        
        return f"{platform}:{clean_id}"
