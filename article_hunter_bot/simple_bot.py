#!/usr/bin/env python3
"""
Simple bot runner for Article Hunter - avoid router conflicts
"""
import asyncio
import logging
import os
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from database import DatabaseManager
from models import User, Keyword
from services.search_service import SearchService
from services.notification_service import NotificationService
from scheduler import PollingScheduler
from zoneinfo import ZoneInfo

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global services
db_manager = None
search_service = None
notification_service = None
polling_scheduler = None  # Will be set by main application

def berlin(dt_utc: datetime | None) -> str:
    """Format datetime in Berlin timezone"""
    if not dt_utc:
        return "/"
    return dt_utc.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M") + " Uhr"

async def ensure_user(telegram_user) -> User:
    """Ensure user exists in database"""
    user = await db_manager.get_user_by_telegram_id(telegram_user.id)
    
    if not user:
        user = User(telegram_id=telegram_user.id)
        await db_manager.create_user(user)
    
    return user

async def cmd_search(message: Message):
    """Handle /search <keyword> command"""
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer(
            "❌ Bitte geben Sie einen Suchbegriff an.\\n\\n"
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
        "🔍 **Suche läuft...**\\n\\n"
        "Führe vollständige Baseline-Suche durch.",
        parse_mode="Markdown"
    )
    
    try:
        # Create keyword subscription
        keyword = Keyword(
            user_id=user.id,
            original_keyword=keyword_text,
            normalized_keyword=normalized,
            since_ts=datetime.utcnow(),  # Set baseline timestamp
            baseline_status="pending",
            platforms=["militaria321.com"]
        )
        await db_manager.create_keyword(keyword)
        
        # Perform full baseline seed with state machine
        baseline_items = await search_service.full_baseline_seed(keyword_text, keyword.id)
        
        # Seed seen_listing_keys with all baseline results
        seen_keys = []
        for item in baseline_items:
            listing_key = f"{item.platform}:{item.platform_id}"
            seen_keys.append(listing_key)
        
        await db_manager.update_keyword_seen_keys(keyword.id, seen_keys)
        
        # Start polling for this keyword
        if polling_scheduler:
            polling_scheduler.add_keyword_job(keyword, user.telegram_id)
        
        # Format response message
        response_text = (
            f"Suche eingerichtet: \"{keyword_text}\"\\n\\n"
            f"✅ Baseline abgeschlossen – Ich benachrichtige Sie künftig nur bei neuen Angeboten.\\n"
            f"⏱️ Frequenz: Alle 60 Sekunden\\n\\n"
            f"📊 {len(seen_keys)} Angebote als Baseline erfasst"
        )
        
        await status_msg.edit_text(response_text, parse_mode="Markdown")
        
        logger.info(f"Search subscription created: '{keyword_text}' with {len(seen_keys)} baseline items")
        
    except Exception as e:
        logger.error(f"Error creating search subscription: {e}")
        await status_msg.edit_text(
            "❌ Fehler beim Einrichten der Suche. Bitte versuchen Sie es erneut."
        )

async def cmd_check(message: Message):
    """Handle /check <keyword> command"""
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer(
            "❌ Bitte geben Sie den zu prüfenden Suchbegriff an.\\n\\n"
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
        "🔍 **Vollsuche läuft...**\\n\\n"
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
        
        response_text = "\\n".join(response_lines)
        await status_msg.edit_text(response_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error performing check: {e}")
        await status_msg.edit_text(
            "❌ Fehler beim Durchsuchen. Bitte versuchen Sie es später erneut."
        )

async def cmd_delete(message: Message):
    """Handle /delete <keyword> command"""
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer(
            "❌ Bitte geben Sie den zu löschenden Suchbegriff an.\\n\\n"
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
        
        # Remove from scheduler
        if polling_scheduler:
            polling_scheduler.remove_keyword_job(keyword.id)
        
        await message.answer(
            f"Überwachung für \"{keyword.original_keyword}\" wurde gelöscht."
        )
        
        logger.info(f"Keyword deleted: '{keyword.original_keyword}' (user {user.id})")
        
    except Exception as e:
        logger.error(f"Error deleting keyword: {e}")
        await message.answer(
            "❌ Fehler beim Löschen. Bitte versuchen Sie es erneut."
        )

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
        
        # Remove from scheduler
        if polling_scheduler:
            polling_scheduler.remove_keyword_job(keyword.id)
        
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

async def cmd_admin_clear(message: Message):
    """Handle /admin clear command - public wipe of stored products"""
    # Parse command to ensure it's "clear"
    args = message.text.split()
    if len(args) < 2 or args[1].lower() != "clear":
        await message.answer("❓ Verwenden Sie `/admin clear` zum Bereinigen der Datenbank.")
        return
    
    user = await ensure_user(message.from_user)
    
    # Create confirmation keyboard
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ja, alles löschen", callback_data="admin_clear_confirm"),
            InlineKeyboardButton(text="❌ Abbrechen", callback_data="admin_clear_cancel")
        ]
    ])
    
    await message.answer(
        "⚠️ Achtung: Dies löscht *alle gespeicherten Angebote und Benachrichtigungen* für alle Nutzer. "
        "Nutzer & Keywords bleiben erhalten. Fortfahren?",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def cmd_clear(message: Message):
    """Handle /clear command - alias for admin clear"""
    user = await ensure_user(message.from_user)
    
    # Create confirmation keyboard
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ja, alles löschen", callback_data="admin_clear_confirm"),
            InlineKeyboardButton(text="❌ Abbrechen", callback_data="admin_clear_cancel")
        ]
    ])
    
    await message.answer(
        "⚠️ Achtung: Dies löscht *alle gespeicherten Angebote und Benachrichtigungen* für alle Nutzer. "
        "Nutzer & Keywords bleiben erhalten. Fortfahren?",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def cmd_list(message: Message):
    """Handle /list command - show health/status of all active keywords"""
    user = await ensure_user(message.from_user)
    
    try:
        # Get active keywords for user
        keywords = await db_manager.get_user_keywords(user.id, active_only=True)
        
        if not keywords:
            await message.answer("Sie haben derzeit keine aktiven Überwachungen.")
            return
        
        # Sort by created_at desc (newest first)
        keywords.sort(key=lambda k: k.created_at, reverse=True)
        
        # Build message with health status
        
        message_lines = ["**Ihre aktiven Überwachungen:**\\n"]
        keyboard_buttons = []
        
        now_utc = datetime.utcnow()
        
        for i, keyword in enumerate(keywords):
            # Compute health status
            status, reason = search_service.compute_keyword_health(keyword, now_utc, polling_scheduler)
            
            # Log health check
            logger.info({
                "event": "kw_health",
                "keyword_id": keyword.id,
                "status": status,
                "reason": reason,
                "consecutive_errors": keyword.consecutive_errors,
                "has_job": polling_scheduler.scheduler_has_job(f"keyword_{keyword.id}"),
                "baseline": keyword.baseline_status,
                "last_success_ts": keyword.last_success_ts.isoformat() if keyword.last_success_ts else None
            })
            
            # Build keyword entry
            keyword_text = f"""📝 **{keyword.original_keyword}**
Status: {status} — {reason}
Letzte Prüfung: {berlin(keyword.last_checked)} — Letzter Erfolg: {berlin(keyword.last_success_ts)}
Baseline: {keyword.baseline_status}
Plattformen: {", ".join(keyword.platforms)}"""
            
            message_lines.append(keyword_text)
            
            # Add inline buttons for this keyword
            buttons_row = [
                InlineKeyboardButton(text="🔍 Diagnostik", callback_data=f"kw_diag:{keyword.id}"),
                InlineKeyboardButton(text="🗑️ Löschen", callback_data=f"kw_del:{keyword.id}")
            ]
            keyboard_buttons.append(buttons_row)
        
        # Create keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Send message
        full_message = "\\n\\n".join(message_lines)
        await message.answer(full_message, reply_markup=keyboard, parse_mode="Markdown")
        
        # Log list render
        logger.info({
            "event": "list_render",
            "user_id": user.id,
            "count": len(keywords)
        })
        
    except Exception as e:
        logger.error(f"Error in /list command: {e}")
        await message.answer("❌ Fehler beim Laden der Überwachungen.")

async def kw_diagnosis(callback: CallbackQuery):
    """Handle keyword diagnosis callback"""
    try:
        keyword_id = callback.data.split(":", 1)[1]
        user = await ensure_user(callback.from_user)
        
        # Find keyword
        keywords = await db_manager.get_user_keywords(user.id, active_only=True)
        keyword = None
        for kw in keywords:
            if kw.id == keyword_id:
                keyword = kw
                break
        
        if not keyword:
            await callback.answer("❌ Suchbegriff nicht gefunden.", show_alert=True)
            return
        
        # Show progress
        await callback.answer(f"🔍 Diagnose läuft für \"{keyword.original_keyword}\"...")
        
        try:
            # Run comprehensive diagnosis
            diagnosis_report = await search_service.diagnose_keyword(keyword, polling_scheduler)
            
            # Send diagnosis report
            await callback.message.reply(diagnosis_report)
            
        except Exception as e:
            logger.error(f"Error in diagnosis: {e}")
            error_msg = str(e)[:100] + "..." if len(str(e)) > 100 else str(e)
            await callback.message.reply(f"❌ Fehler bei der Diagnose: {error_msg}")
        
    except Exception as e:
        logger.error(f"Error in kw_diagnosis: {e}")
        await callback.answer("❌ Fehler bei der Diagnose.", show_alert=True)

async def kw_delete_callback(callback: CallbackQuery):
    """Handle keyword deletion callback - reuse existing delete flow"""
    try:
        keyword_id = callback.data.split(":", 1)[1]
        user = await ensure_user(callback.from_user)
        
        # Find keyword
        keywords = await db_manager.get_user_keywords(user.id, active_only=True)
        keyword = None
        for kw in keywords:
            if kw.id == keyword_id:
                keyword = kw
                break
        
        if not keyword:
            await callback.answer("❌ Suchbegriff nicht gefunden.", show_alert=True)
            return
        
        # Delete keyword and remove from scheduler
        await db_manager.delete_keyword(keyword.id)
        
        if polling_scheduler:
            polling_scheduler.remove_keyword_job(keyword.id)
        
        await callback.answer(f"✅ '{keyword.original_keyword}' gelöscht.", show_alert=True)
        
        # Optionally refresh the list or remove the buttons
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass  # Message might be too old to edit
            
    except Exception as e:
        logger.error(f"Error in kw_delete_callback: {e}")
        await callback.answer("❌ Fehler beim Löschen.", show_alert=True)

async def admin_clear_confirm(callback: CallbackQuery):
    """Handle admin clear confirmation"""
    try:
        user = await ensure_user(callback.from_user)
        
        # Perform the clear operation
        result = await db_manager.admin_clear_products()
        
        # Log the action
        logger.warning({
            "event": "admin_clear",
            "by_user": callback.from_user.id,
            "scope": ["listings", "keyword_hits", "notifications"],
            "deleted_counts": result
        })
        
        # Send success message
        await callback.message.edit_text(
            f"🧹 Bereinigung abgeschlossen.\n"
            f"• Listings: {result['listings']}\n"
            f"• Keyword-Treffer: {result['keyword_hits']}\n"
            f"• Benachrichtigungen: {result['notifications']}"
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in admin clear confirm: {e}")
        await callback.message.edit_text("❌ Fehler bei der Bereinigung aufgetreten.")
        await callback.answer()

async def admin_clear_cancel(callback: CallbackQuery):
    """Handle admin clear cancellation"""
    await callback.message.edit_text("❌ Abgebrochen.")
    await callback.answer()

async def main():
    """Main bot function"""
    global db_manager, search_service, notification_service, polling_scheduler
    
    # Initialize database
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    # Initialize bot
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in environment")
    
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    # Initialize services
    search_service = SearchService(db_manager)
    notification_service = NotificationService(db_manager, bot)
    
    # Initialize scheduler
    polling_scheduler = PollingScheduler(db_manager, search_service, notification_service)
    await polling_scheduler.start()
    
    # Create dispatcher and router
    dp = Dispatcher()
    router = Router()
    
    # Register handlers
    router.message.register(cmd_search, Command("search"))
    router.message.register(cmd_check, Command("check"))
    router.message.register(cmd_delete, Command("delete"))
    router.message.register(cmd_list, Command("list"))  # New list command
    
    # Admin clear handlers (public access)
    router.message.register(cmd_admin_clear, Command("admin"))
    router.message.register(cmd_clear, Command("clear"))  # Alias for convenience
    router.callback_query.register(admin_clear_confirm, F.data == "admin_clear_confirm")
    router.callback_query.register(admin_clear_cancel, F.data == "admin_clear_cancel")
    
    # List command callbacks
    router.callback_query.register(kw_diagnosis, F.data.startswith("kw_diag:"))
    router.callback_query.register(kw_delete_callback, F.data.startswith("kw_del:"))
    
    # Callback handlers
    router.callback_query.register(handle_delete_keyword_callback, F.data.startswith("delete_keyword_"))
    
    # Include router
    dp.include_router(router)
    
    try:
        logger.info("Starting Article Hunter Bot...")
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        # Cleanup
        logger.info("Shutting down...")
        await polling_scheduler.stop()
        await db_manager.close()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped")