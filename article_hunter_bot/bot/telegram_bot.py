import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from database import DatabaseManager
from models import User, Keyword
from services.search_service import SearchService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# Create router for handlers
router = Router()

# Global services (set by main application)
db_manager: Optional[DatabaseManager] = None
search_service: Optional[SearchService] = None
notification_service: Optional[NotificationService] = None
polling_scheduler = None  # Will be set by main application


def set_services(db_mgr: DatabaseManager, search_svc: SearchService, notif_svc: NotificationService, scheduler=None):
    """Set services from main application"""
    global db_manager, search_service, notification_service, polling_scheduler
    db_manager = db_mgr
    search_service = search_svc
    notification_service = notif_svc
    polling_scheduler = scheduler


@router.message(Command("search"))
async def cmd_search(message: Message):
    """Handle /search <keyword> command
    
    Create subscription, run full baseline crawl, seed seen_listing_keys,
    set since_ts, start 60s polling
    """
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer(
            "❌ Bitte geben Sie einen Suchbegriff an.\n\n"
            "Beispiel: `/search Wehrmacht Helm`",
            parse_mode="Markdown"
        )
        return
    
    keyword_text = args[1].strip()
    if not keyword_text:
        await message.answer("❌ Suchbegriff darf nicht leer sein.")
        return
    
    if len(keyword_text) > 100:
        await message.answer("❌ Suchbegriff ist zu lang (max. 100 Zeichen).")
        return
    
    # Check if keyword already exists
    normalized = SearchService.normalize_keyword(keyword_text)
    existing = await db_manager.get_keyword_by_normalized(user.id, normalized)
    if existing:
        await message.answer(
            f"⚠️ Suchbegriff **'{existing.original_keyword}'** existiert bereits.",
            parse_mode="Markdown"
        )
        return
    
    # Show "searching" message
    status_msg = await message.answer(
        "🔍 **Suche läuft...**\n\n"
        "Führe vollständige Baseline-Suche durch.",
        parse_mode="Markdown"
    )
    
    try:
        # Create keyword subscription
        keyword = Keyword(
            user_id=user.id,
            original_keyword=keyword_text,
            normalized_keyword=normalized,
            since_ts=datetime.utcnow()  # Set baseline timestamp
        )
        await db_manager.create_keyword(keyword)
        
        # Perform full baseline crawl across ALL pages
        baseline_items = await search_service.full_baseline_crawl(keyword_text)
        
        # Seed seen_listing_keys with all baseline results
        seen_keys = []
        for item in baseline_items:
            listing_key = f"{item.platform}:{item.platform_id}"
            seen_keys.append(listing_key)
        
        await db_manager.update_keyword_seen_keys(keyword.id, seen_keys)
        
        # Format response message
        response_text = (
            f"Suche eingerichtet: \"{keyword_text}\"\n\n"
            f"✅ Baseline abgeschlossen – Ich benachrichtige Sie künftig nur bei neuen Angeboten.\n"
            f"⏱️ Frequenz: Alle 60 Sekunden"
        )
        
        await status_msg.edit_text(response_text)
        
        # Start polling for this keyword
        if polling_scheduler:
            polling_scheduler.add_keyword_job(keyword, user.telegram_id)
        
        logger.info(f"Search subscription created: '{keyword_text}' with {len(seen_keys)} baseline items")
        
    except Exception as e:
        logger.error(f"Error creating search subscription: {e}")
        await status_msg.edit_text(
            "❌ Fehler beim Einrichten der Suche. Bitte versuchen Sie es erneut."
        )


@router.message(Command("check"))
async def cmd_check(message: Message):
    """Handle /check <keyword> command
    
    Run full re-scan, update DB, return page/item counts, no notifications
    """
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer(
            "❌ Bitte geben Sie den zu prüfenden Suchbegriff an.\n\n"
            "Beispiel: `/check Wehrmacht Helm`",
            parse_mode="Markdown"
        )
        return
    
    keyword_text = args[1].strip()
    if not keyword_text:
        await message.answer("❌ Suchbegriff darf nicht leer sein.")
        return
    
    # Show "checking" message
    status_msg = await message.answer(
        "🔍 **Vollsuche läuft...**\n\n"
        "Durchsuche alle Seiten für aktuelle Treffer.",
        parse_mode="Markdown"
    )
    
    try:
        # Perform full re-scan
        results = await search_service.full_recheck_crawl(keyword_text)
        
        # Format response with page/item counts per provider
        response_lines = [f"Vollsuche abgeschlossen: \"{keyword_text}\""]
        
        for platform_name in sorted(results.keys()):
            result = results[platform_name]
            if result.get("error"):
                response_lines.append(f"• **{platform_name}**: Fehler: {result['error']}")
            else:
                response_lines.append(
                    f"• **{platform_name}**: {result['pages_scanned']} Seiten, {result['total_count']} Produkte"
                )
        
        response_text = "\n".join(response_lines)
        await status_msg.edit_text(response_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error performing check: {e}")
        await status_msg.edit_text(
            "❌ Fehler beim Durchsuchen. Bitte versuchen Sie es später erneut."
        )


@router.message(Command("delete"))
async def cmd_delete(message: Message):
    """Handle /delete <keyword> command
    
    Remove subscription and stop scheduled job
    """
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer(
            "❌ Bitte geben Sie den zu löschenden Suchbegriff an.\n\n"
            "Beispiel: `/delete Wehrmacht Helm`",
            parse_mode="Markdown"
        )
        return
    
    keyword_text = args[1].strip()
    if not keyword_text:
        await message.answer("❌ Suchbegriff darf nicht leer sein.")
        return
    
    # Find keyword
    normalized = SearchService.normalize_keyword(keyword_text)
    keyword = await db_manager.get_keyword_by_normalized(user.id, normalized)
    
    if not keyword:
        await message.answer(
            f"❌ Suchbegriff **'{keyword_text}'** nicht gefunden.",
            parse_mode="Markdown"
        )
        return
    
    try:
        # Delete keyword (scheduler will handle job removal)
        await db_manager.delete_keyword(keyword.id)
        
        await message.answer(
            f"Überwachung für \"{keyword.original_keyword}\" wurde gelöscht."
        )
        
        logger.info(f"Keyword deleted: '{keyword.original_keyword}' (user {user.id})")
        
    except Exception as e:
        logger.error(f"Error deleting keyword: {e}")
        await message.answer(
            "❌ Fehler beim Löschen. Bitte versuchen Sie es erneut."
        )


@router.callback_query(F.data.startswith("delete_keyword_"))
async def handle_delete_keyword_callback(callback: CallbackQuery):
    """Handle 'Keyword löschen' button from notifications"""
    try:
        keyword_id = callback.data.split("_")[-1]
        user = await ensure_user(callback.from_user)
        
        # Find and delete keyword
        keywords = await db_manager.get_user_keywords(user.id, active_only=False)
        keyword = None
        for kw in keywords:
            if kw.id == keyword_id:
                keyword = kw
                break
        
        if not keyword:
            await callback.answer("❌ Suchbegriff nicht gefunden.", show_alert=True)
            return
        
        # Delete keyword
        await db_manager.delete_keyword(keyword.id)
        
        await callback.answer(
            f"✅ Suchbegriff '{keyword.original_keyword}' gelöscht.",
            show_alert=True
        )
        
        # Edit message to show it's deleted
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass  # Message might be too old to edit
        
    except Exception as e:
        logger.error(f"Error handling delete callback: {e}")
        await callback.answer("❌ Fehler beim Löschen.", show_alert=True)


async def ensure_user(telegram_user) -> User:
    """Ensure user exists in database"""
    user = await db_manager.get_user_by_telegram_id(telegram_user.id)
    
    if not user:
        user = User(telegram_id=telegram_user.id)
        await db_manager.create_user(user)
    
    return user


class TelegramBotManager:
    """Main Telegram bot manager"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.bot = None
        self.dp = None
        self.search_service = None
        self.notification_service = None
        self.is_running = False
    
    async def initialize(self):
        """Initialize bot and services"""
        token = os.environ.get('TELEGRAM_BOT_TOKEN')
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment")
        
        # Create bot
        self.bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        
        # Initialize services
        self.search_service = SearchService(self.db)
        self.notification_service = NotificationService(self.db, self.bot)
        
        # Set services in handlers
        set_services(self.db, self.search_service, self.notification_service)
        
        # Create dispatcher
        self.dp = Dispatcher()
        self.dp.include_router(router)
        
        logger.info("Telegram bot initialized")
    
    async def start(self):
        """Start the bot"""
        if self.is_running:
            return
        
        await self.initialize()
        
        self.is_running = True
        logger.info("Starting Telegram bot...")
        
        # Start polling
        await self.dp.start_polling(self.bot)
    
    async def stop(self):
        """Stop the bot"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self.dp:
            await self.dp.stop_polling()
        
        if self.bot:
            await self.bot.session.close()
        
        logger.info("Telegram bot stopped")
