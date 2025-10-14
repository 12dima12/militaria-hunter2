import logging
import os
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import DatabaseManager
from models import Keyword, Listing, Notification
from utils.text import br_join, b, i, a, code, fmt_ts_de, fmt_price_de, safe_truncate

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending German-formatted notifications"""
    
    def __init__(self, db_manager: DatabaseManager, bot: Bot):
        self.db = db_manager
        self.bot = bot
        raw_admin_chat = os.environ.get("NOTIFY_ADMIN_CHAT_ID", "").strip()
        self.admin_chat_id: Optional[int] = int(raw_admin_chat) if raw_admin_chat.isdigit() else None
    
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

    async def send_recaptcha_warning(self, user_telegram_id: int, keyword: Keyword, event: dict) -> None:
        """Send user-facing warning when Kleinanzeigen triggers bot protection."""

        lines = [
            f"⚠️ {b('Kleinanzeigen Bot-Schutz')}",
            f"Suchbegriff: {keyword.original_keyword}",
        ]

        page = event.get("page")
        if page is not None:
            lines.append(f"Seite: {page}")

        status = event.get("status")
        if status is not None:
            lines.append(f"Status: {status}")

        final_url = event.get("url")
        if final_url:
            lines.append(f"URL: {final_url}")

        lines.append(
            "Kleinanzeigen hat einen Bot-Schutz aktiviert. Die Suche kann vorübergehend eingeschränkt sein."
        )

        payload = br_join(lines)

        try:
            await self.bot.send_message(
                chat_id=user_telegram_id,
                text=payload,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            logger.info(
                {
                    "event": "recaptcha_warning_sent",
                    "keyword": keyword.normalized_keyword,
                    "user": user_telegram_id,
                }
            )
        except Exception as exc:
            logger.error(
                {
                    "event": "recaptcha_warning_failed",
                    "keyword": keyword.normalized_keyword,
                    "user": user_telegram_id,
                    "error": str(exc)[:200],
                }
            )

    def _format_notification_message(self, keyword: Keyword, item: Listing) -> str:
        """Format notification message with German texts and Berlin timezone

        Template as specified in requirements
        """
        # Format price and timestamps using utilities
        preis = fmt_price_de(item.price_value, item.price_currency)
        if preis == "/" and getattr(item, "price_text", None):
            preis = item.price_text
        gefunden = fmt_ts_de(datetime.now(ZoneInfo('UTC')))
        inseriert_am = fmt_ts_de(item.posted_ts)
        platform_label = item.platform
        platform_link = a(platform_label, item.url) if item.url else platform_label

        # Build message with proper HTML formatting
        return br_join([
            f"🔎 {b('Neues Angebot gefunden')}",
            "",
            f"Suchbegriff: {keyword.original_keyword}",
            f"Titel: {safe_truncate(item.title, 80)}",
            f"Preis: {preis}",
            f"Plattform: {platform_link}",
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

    async def send_admin_event(self, event: dict) -> None:
        """Send administrative diagnostics event if chat configured"""
        if not self.admin_chat_id:
            return

        lines = [
            f"⚠️ {b('Systemmeldung')}",
            f"Plattform: {event.get('platform', 'unbekannt')}",
            f"Status: {event.get('state', 'n/a')}"
        ]

        first_seen = event.get("first_seen")
        if first_seen:
            lines.append(f"Erkannt: {first_seen}")

        recovered_at = event.get("recovered_at")
        if recovered_at:
            lines.append(f"Wieder normal: {recovered_at}")

        cooldown_minutes = event.get("cooldown_minutes")
        if cooldown_minutes:
            lines.append(f"Cooldown: {int(cooldown_minutes)} Minuten")

        cooldown_until = event.get("cooldown_until")
        if cooldown_until:
            lines.append(f"Gesperrt bis: {cooldown_until}")

        payload = br_join(lines)

        try:
            await self.bot.send_message(
                chat_id=self.admin_chat_id,
                text=payload,
                parse_mode="HTML",
            )
            logger.info({"event": "admin_event", "payload": payload[:120].replace("\n", "⏎")})
        except Exception as exc:
            logger.error({"event": "admin_event_failed", "error": str(exc)[:200]})
