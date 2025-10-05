#!/usr/bin/env python3
"""
Simple bot runner for Article Hunter - avoid router conflicts
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
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
from scheduler import PollingScheduler, stop_keyword_job
from zoneinfo import ZoneInfo
from providers.militaria321 import Militaria321Provider
from utils.text import br_join, b, i, a, code, fmt_ts_de, fmt_price_de, safe_truncate

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

async def _format_verification_block(last_item_meta: dict, keyword_text: str, search_service) -> str:
    """Format verification block for last found listing"""
    listing = last_item_meta["listing"]
    page_index = last_item_meta["page_index"]
    
    # If posted_ts is missing, fetch it for display only
    had_to_fetch_detail = False
    if listing.posted_ts is None:
        try:
            provider = search_service.providers.get("militaria321.com")
            if provider:
                await provider.fetch_posted_ts_batch([listing], concurrency=1)
                had_to_fetch_detail = True
        except Exception as e:
            logger.warning(f"Failed to fetch posted_ts for verification: {e}")
    
    # Log verification event
    logger.info({
        "event": "verification_last_item",
        "platform": listing.platform,
        "page_index": page_index,
        "listing_key": f"{listing.platform}:{listing.platform_id}",
        "posted_ts_utc": listing.posted_ts.isoformat() if listing.posted_ts else None,
        "had_to_fetch_detail": had_to_fetch_detail
    })
    
    # Format timestamps and price using utilities
    now_berlin = fmt_ts_de(datetime.now(timezone.utc))
    posted_berlin = fmt_ts_de(listing.posted_ts)
    price_formatted = fmt_price_de(listing.price_value, listing.price_currency)
    
    # Build verification block using HTML formatting
    return br_join([
        f"🎖️ Der letzte gefundene Artikel auf Seite {page_index}",
        "",
        f"🔍 Suchbegriff: {keyword_text}",
        f"📝 Titel: {safe_truncate(listing.title, 80)}",
        f"💰 {price_formatted}",
        "",
        f"🌐 Plattform: {a('militaria321.com', listing.url)}",
        f"🕐 Gefunden: {now_berlin}",
        f"✏️ Eingestellt am: {posted_berlin}"
    ])

async def cmd_search(message: Message):
    """Handle /search <keyword> command"""
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        text = br_join([
            "❌ Bitte geben Sie einen Suchbegriff an.",
            "",
            f"Beispiel: {code('/search Wehrmacht Helm')}"
        ])
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    keyword_text = args[1].strip()
    if not keyword_text:
        text = "❌ Suchbegriff darf nicht leer sein."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    if len(keyword_text) > 100:
        text = "❌ Suchbegriff ist zu lang (max. 100 Zeichen)."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    # Check if keyword already exists
    normalized = SearchService.normalize_keyword(keyword_text)
    existing = await db_manager.get_keyword_by_normalized(user.id, normalized)
    
    # Log duplicate check for debugging
    logger.info({
        "event": "dup_check",
        "user_id": user.id,
        "normalized": normalized,
        "found_doc_id": existing.id if existing else None,
        "is_active": existing.is_active if existing else None,
        "status_fields": {
            "baseline_status": existing.baseline_status if existing else None,
            "last_checked": existing.last_checked.isoformat() if existing and existing.last_checked else None
        } if existing else None
    })
    
    if existing:
        if existing.is_active:
            # Truly active keyword exists
            text = f"⚠️ Suchbegriff {b(existing.original_keyword)} existiert bereits."
            await message.answer(text, parse_mode="HTML")
            logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
            return
        else:
            # Inactive keyword exists - reactivate it
            logger.info(f"Reactivating inactive keyword: {existing.original_keyword}")
            
            # Reset keyword for reactivation
            existing.is_active = True
            existing.since_ts = datetime.utcnow()
            existing.seen_listing_keys = []
            existing.baseline_status = "pending"
            existing.baseline_errors = {}
            existing.last_checked = None
            existing.last_success_ts = None
            existing.last_error_ts = None
            existing.consecutive_errors = 0
            existing.updated_at = datetime.utcnow()
            
            # Update in database
            update_doc = existing.dict()
            await db_manager.db.keywords.update_one(
                {"id": existing.id},
                {"$set": update_doc}
            )
            
            # Reschedule job
            if polling_scheduler:
                polling_scheduler.add_keyword_job(existing, user.telegram_id)
            
            text = f"✅ Suchbegriff reaktiviert: {b(existing.original_keyword)} – Baseline wird neu aufgebaut."
            await message.answer(text, parse_mode="HTML")
            logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
            return
    
    # Show "searching" message
    search_text = br_join([
        f"🔍 {b('Suche läuft...')}",
        "",
        "Führe vollständige Baseline-Suche durch."
    ])
    status_msg = await message.answer(search_text, parse_mode="HTML")
    logger.info({"event": "send_text", "len": len(search_text), "preview": search_text[:120].replace("\n", "⏎")})
    
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
        baseline_items, last_item_meta = await search_service.full_baseline_seed(keyword_text, keyword.id)
        
        # Seed seen_listing_keys with all baseline results
        seen_keys = []
        for item in baseline_items:
            listing_key = f"{item.platform}:{item.platform_id}"
            seen_keys.append(listing_key)
        
        await db_manager.update_keyword_seen_keys(keyword.id, seen_keys)
        
        # Start polling for this keyword
        if polling_scheduler:
            polling_scheduler.add_keyword_job(keyword, user.telegram_id)
        
        # Format main response message
        response_lines = [
            f"Suche eingerichtet: {b(keyword_text)}",
            "",
            "✅ Baseline abgeschlossen – Ich benachrichtige Sie künftig nur bei neuen Angeboten.",
            "⏱️ Frequenz: Alle 60 Sekunden",
            "",
            f"📊 {len(seen_keys)} Angebote als Baseline erfasst"
        ]
        
        # Add verification block if we have a last item
        if last_item_meta and last_item_meta.get("listing"):
            verification_text = await _format_verification_block(
                last_item_meta, keyword_text, search_service
            )
            response_lines.extend(["", verification_text])
        
        response_text = br_join(response_lines)
        await status_msg.edit_text(response_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(response_text), "preview": response_text[:120].replace("\n", "⏎")})
        
        logger.info(f"Search subscription created: '{keyword_text}' with {len(seen_keys)} baseline items")
        
    except Exception as e:
        logger.error(f"Error creating search subscription: {e}")
        error_text = "❌ Fehler beim Einrichten der Suche. Bitte versuchen Sie es erneut."
        await status_msg.edit_text(error_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(error_text), "preview": error_text[:120].replace("\n", "⏎")})

async def cmd_check(message: Message):
    """Handle /check <keyword> command"""
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        text = br_join([
            "❌ Bitte geben Sie den zu prüfenden Suchbegriff an.",
            "",
            f"Beispiel: {code('/check Wehrmacht Helm')}"
        ])
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    keyword_text = args[1].strip()
    if not keyword_text:
        text = "❌ Suchbegriff darf nicht leer sein."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    # Show "checking" message
    check_text = br_join([
        f"🔍 {b('Vollsuche läuft...')}",
        "",
        "Durchsuche alle Seiten für aktuelle Treffer."
    ])
    status_msg = await message.answer(check_text, parse_mode="HTML")
    logger.info({"event": "send_text", "len": len(check_text), "preview": check_text[:120].replace("\n", "⏎")})
    
    try:
        # Perform full re-scan
        results = await search_service.full_recheck_crawl(keyword_text)
        
        # Format response with page/item counts per provider
        response_lines = [f"Vollsuche abgeschlossen: {b(keyword_text)}", ""]
        
        for platform_name in sorted(results.keys()):
            result = results[platform_name]
            if result.get("error"):
                response_lines.append(f"• {b(platform_name)}: Fehler: {result['error']}")
            else:
                response_lines.append(
                    f"• {b(platform_name)}: {result['pages_scanned']} Seiten, {result['total_count']} Produkte"
                )
        
        response_text = br_join(response_lines)
        await status_msg.edit_text(response_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(response_text), "preview": response_text[:120].replace("\n", "⏎")})
        
    except Exception as e:
        logger.error(f"Error performing check: {e}")
        error_text = "❌ Fehler beim Durchsuchen. Bitte versuchen Sie es später erneut."
        await status_msg.edit_text(error_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(error_text), "preview": error_text[:120].replace("\n", "⏎")})

async def cmd_delete(message: Message):
    """Handle /delete <keyword> command"""
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        text = br_join([
            "❌ Bitte geben Sie den zu löschenden Suchbegriff an.",
            "",
            f"Beispiel: {code('/delete Wehrmacht Helm')}"
        ])
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    keyword_text = args[1].strip()
    if not keyword_text:
        text = "❌ Suchbegriff darf nicht leer sein."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    # Find keyword (only active ones)
    normalized = SearchService.normalize_keyword(keyword_text)
    keyword = await db_manager.get_keyword_by_normalized(user.id, normalized, active_only=True)
    
    if not keyword:
        text = f"❌ Suchbegriff {b(keyword_text)} nicht gefunden."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    try:
        # Soft delete keyword (set is_active = False)
        await db_manager.soft_delete_keyword(keyword.id)
        
        # Remove from scheduler
        if polling_scheduler:
            polling_scheduler.remove_keyword_job(keyword.id)
        
        text = f"Überwachung für {b(keyword.original_keyword)} wurde gelöscht."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        
        logger.info({
            "event": "keyword_soft_deleted",
            "keyword_id": keyword.id,
            "keyword": keyword.original_keyword,
            "user_id": user.id
        })
        
    except Exception as e:
        logger.error(f"Error deleting keyword: {e}")
        text = "❌ Fehler beim Löschen. Bitte versuchen Sie es erneut."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})

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
    """Handle /clear (user-specific) and /clear data (global wipe alias)"""
    user = await ensure_user(message.from_user)
    
    text = (message.text or "").strip().lower()
    
    if text.endswith(" data"):
        # Optional: legacy/global wipe confirm
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ja, alle Daten löschen", callback_data="clear_data_confirm"),
            InlineKeyboardButton(text="❌ Abbrechen", callback_data="clear_cancel"),
        ]])
        confirm_text = br_join([
            "⚠️ Achtung: Dies löscht alle gespeicherten Angebote und Benachrichtigungen für alle Nutzer.",
            "Nutzer & Keywords bleiben erhalten. Fortfahren?"
        ])
        await message.answer(confirm_text, reply_markup=kb, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(confirm_text), "preview": confirm_text[:120].replace("\n", "⏎")})
        return

    # Default: delete MY keywords
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ja, alle meine Suchbegriffe löschen", callback_data="clear_my_keywords_confirm"),
        InlineKeyboardButton(text="❌ Abbrechen", callback_data="clear_cancel"),
    ]])
    confirm_text = "Möchten Sie wirklich alle Ihre Suchbegriffe löschen? Dies stoppt auch die Hintergrundüberwachung."
    await message.answer(confirm_text, reply_markup=kb, parse_mode="HTML")
    logger.info({"event": "send_text", "len": len(confirm_text), "preview": confirm_text[:120].replace("\n", "⏎")})

async def cmd_list(message: Message):
    """Handle /list command - show health/status of all active keywords"""
    user = await ensure_user(message.from_user)
    
    try:
        # Get active keywords for user
        keywords = await db_manager.get_user_keywords(user.id, active_only=True)
        
        if not keywords:
            text = "Sie haben derzeit keine aktiven Überwachungen."
            await message.answer(text, parse_mode="HTML")
            logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
            return
        
        # Sort by created_at desc (newest first)
        keywords.sort(key=lambda k: k.created_at, reverse=True)
        
        # Build message with health status
        message_lines = [b("Ihre aktiven Überwachungen:"), ""]
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
            
            # Build keyword entry with proper formatting
            keyword_lines = [
                f"📝 {b(keyword.original_keyword)}",
                f"Status: {status} — {reason}",
                f"Letzte Prüfung: {fmt_ts_de(keyword.last_checked)} — Letzter Erfolg: {fmt_ts_de(keyword.last_success_ts)}",
                f"Baseline: {keyword.baseline_status}",
                f"Plattformen: {', '.join(keyword.platforms)}"
            ]
            
            message_lines.append(br_join(keyword_lines))
            
            # Add inline buttons for this keyword
            buttons_row = [
                InlineKeyboardButton(text="🔍 Diagnostik", callback_data=f"kw_diag:{keyword.id}"),
                InlineKeyboardButton(text="🗑️ Löschen", callback_data=f"kw_del:{keyword.id}")
            ]
            keyboard_buttons.append(buttons_row)
        
        # Create keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Send message
        full_message = br_join(message_lines)
        await message.answer(full_message, reply_markup=keyboard, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(full_message), "preview": full_message[:120].replace("\n", "⏎")})
        
        # Log list render
        logger.info({
            "event": "list_render",
            "user_id": user.id,
            "count": len(keywords)
        })
        
    except Exception as e:
        logger.error(f"Error in /list command: {e}")
        text = "❌ Fehler beim Laden der Überwachungen."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})

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
        
        # Soft delete keyword and remove from scheduler
        await db_manager.soft_delete_keyword(keyword.id)
        
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

async def clear_my_keywords_confirm(callback: CallbackQuery):
    """Handle user-specific keyword deletion confirmation"""
    user = await ensure_user(callback.from_user)
    
    kw_ids = await db_manager.get_user_keyword_ids(user.id)
    if not kw_ids:
        await callback.message.edit_text("Sie haben derzeit keine Suchbegriffe.")
        await callback.answer()
        return

    # Log what keywords we're about to delete
    logger.info({
        "event": "clear_my_keywords_start",
        "user_id": user.id,
        "keyword_ids_to_delete": kw_ids,
        "count": len(kw_ids)
    })

    # Stop jobs (idempotent)
    stopped = 0
    for kw_id in kw_ids:
        # Use the actual job ID format used by the scheduler
        if stop_keyword_job(f"keyword_{kw_id}"):
            stopped += 1

    # Delete artifacts first, then keywords (order prevents dangling refs)
    n_hits = await db_manager.delete_keyword_hits_by_keyword_ids(kw_ids)
    n_notifs = await db_manager.delete_notifications_by_keyword_ids(kw_ids)
    n_kw = await db_manager.delete_keywords_by_ids(kw_ids)

    logger.warning({
        "event": "clear_my_keywords_result",
        "user_id": user.id,
        "kw_deleted": n_kw,
        "jobs_stopped": stopped,
        "hits_deleted": n_hits,
        "notifs_deleted": n_notifs,
        "keyword_ids_targeted": kw_ids
    })

    await callback.message.edit_text(
        f"🧹 Bereinigung abgeschlossen.\n"
        f"• Keywords: {n_kw}\n"
        f"• Gestoppte Jobs: {stopped}\n"
        f"• Keyword-Treffer: {n_hits}\n"
        f"• Benachrichtigungen: {n_notifs}"
    )
    await callback.answer()

async def clear_data_confirm(callback: CallbackQuery):
    """Handle global data wipe confirmation"""
    # Call your existing global wipe function (listings/notifications/hits)
    try:
        res = await db_manager.admin_clear_products()  # existing method
        await callback.message.edit_text(
            f"🧹 Bereinigung abgeschlossen.\n"
            f"• Listings: {res.get('listings', 0)}\n"
            f"• Keyword-Treffer: {res.get('keyword_hits', 0)}\n"
            f"• Benachrichtigungen: {res.get('notifications', 0)}"
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ Fehler beim Löschen: {str(e)[:200]}")
    await callback.answer()

async def clear_cancel(callback: CallbackQuery):
    """Handle clear operation cancellation"""
    await callback.message.edit_text("❌ Abgebrochen.")
    await callback.answer()

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
    router.message.register(cmd_clear, Command("clear"))  # New behavior: user-specific or global
    
    # Clear callback handlers
    router.callback_query.register(clear_my_keywords_confirm, F.data == "clear_my_keywords_confirm")
    router.callback_query.register(clear_data_confirm, F.data == "clear_data_confirm")
    router.callback_query.register(clear_cancel, F.data == "clear_cancel")
    
    # Legacy admin clear callbacks (for cmd_admin_clear)
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