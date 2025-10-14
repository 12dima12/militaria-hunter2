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
from services.search_service import SearchService, POLL_INTERVAL_SECONDS
from services.notification_service import NotificationService
from scheduler import PollingScheduler, stop_keyword_job
from zoneinfo import ZoneInfo
from providers.militaria321 import Militaria321Provider
from utils.text import br_join, b, i, a, code, fmt_ts_de, fmt_price_de, htmlesc
from utils.time_utils import now_utc as utc_now

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
            existing.since_ts = utc_now()
            existing.seen_listing_keys = []
            existing.baseline_status = "pending"
            existing.baseline_errors = {}
            existing.last_checked = None
            existing.last_success_ts = None
            existing.last_error_ts = None
            existing.consecutive_errors = 0
            existing.updated_at = utc_now()
            
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
        provider_platforms = list(search_service.providers.keys()) if search_service else ["militaria321.com", "egun.de"]
        keyword = Keyword(
            user_id=user.id,
            original_keyword=keyword_text,
            normalized_keyword=normalized,
            since_ts=utc_now(),  # Set baseline timestamp
            baseline_status="pending",
            platforms=provider_platforms
        )
        await db_manager.create_keyword(keyword)
        
        # Perform full baseline seed with state machine
        baseline_items, _ = await search_service.full_baseline_seed(keyword_text, keyword.id)
        
        # Seed seen_listing_keys with all baseline results
        seen_keys = []
        for item in baseline_items:
            listing_key = f"{item.platform}:{item.platform_id}"
            seen_keys.append(listing_key)
        
        await db_manager.update_keyword_seen_keys(keyword.id, seen_keys)
        
        # Reload keyword from database to get updated seen_listing_keys
        updated_keyword_doc = await db_manager.db.keywords.find_one({"id": keyword.id})
        updated_keyword = Keyword(**updated_keyword_doc)
        
        # Start polling for this keyword
        if polling_scheduler:
            polling_scheduler.add_keyword_job(updated_keyword, user.telegram_id)
        
        # Format main response message
        response_text = br_join([
            f"Suche eingerichtet: <b>{htmlesc(keyword_text)}</b>",
            "✅ <b>Baseline abgeschlossen</b> – Ich benachrichtige Sie künftig nur bei neuen Angeboten.",
            f"⏱️ Frequenz: Alle {POLL_INTERVAL_SECONDS} Sekunden",
            f"📊 {len(seen_keys)} Angebote als Baseline erfasst",
        ])
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
        # Perform manual backfill and verification
        result = await search_service.manual_backfill_check(keyword_text, user.id)
        
        if result.get("error"):
            error_text = f"❌ {result['error']}"
            await status_msg.edit_text(error_text, parse_mode="HTML")
            logger.info({"event": "send_text", "len": len(error_text), "preview": error_text[:120].replace("\n", "⏎")})
            return
        
        providers = result.get("providers", {})
        backfill = result.get("backfill", {})

        platform_urls = {
            "militaria321.com": "https://www.militaria321.com",
            "egun.de": "https://www.egun.de/market",
            "kleinanzeigen.de": "https://www.kleinanzeigen.de",
        }

        response_lines = [
            f"🔎 <b>Manuelle Verifikation abgeschlossen:</b> {htmlesc(keyword_text)}",
            "",
            "📈 <b>Suchergebnisse:</b>",
        ]

        provider_order = list(search_service.providers.keys())

        for platform_name in provider_order:
            data = providers.get(platform_name, {})
            label = htmlesc(platform_name)
            url = platform_urls.get(platform_name)

            enabled = data.get("enabled", True)
            if not enabled:
                response_lines.append(f"• Plattform: {label} — (deaktiviert)")
                response_lines.append("")
                continue

            if url:
                response_lines.append(f"• Plattform: <a href=\"{htmlesc(url)}\">{label}</a>")
            else:
                response_lines.append(f"• Plattform: {label}")

            response_lines.append(
                f"  Seiten: {data.get('pages', 0)} — Artikel: {data.get('items', 0)}"
            )

            unseen_candidates = data.get("unseen_candidates")
            pushed = data.get("pushed")
            already_known = data.get("already_known")
            if unseen_candidates is not None or pushed is not None or already_known is not None:
                response_lines.append(
                    "  "
                    + " — ".join([
                        f"Ungeprüft: {unseen_candidates or 0}",
                        f"Neu gesendet: {pushed or 0}",
                        f"Bereits bekannt: {already_known or 0}",
                    ])
                )

            errors = data.get("errors") or data.get("last_error")
            if errors:
                response_lines.append(f"  Fehler: {htmlesc(errors)}")
            else:
                response_lines.append("  Fehler: Keine")

            since_ts = data.get("since_ts")
            if since_ts:
                response_lines.append(f"  since_ts: {htmlesc(since_ts)}")

            if data.get("cooldown_active"):
                cooldown_until = data.get("cooldown_until") or "unbekannt"
                response_lines.append(
                    f"  ⚠️ Cooldown aktiv bis {htmlesc(str(cooldown_until))}"
                )

            response_lines.append("")

        if response_lines and response_lines[-1] == "":
            response_lines.pop()

        unprocessed = backfill.get("unprocessed", 0)
        new_notifications = backfill.get("new_notifications", 0)
        already_known = backfill.get("already_known", 0)

        response_lines.extend([
            "",
            "🔁 <b>Nachbearbeitung (Backfill):</b>",
            f"• Unverarbeitete Artikel: {unprocessed}",
            f"• Neue Benachrichtigungen: {new_notifications}",
            f"• Bereits bekannte Artikel: {already_known}",
            "",
        ])

        if new_notifications > 0:
            response_lines.append(
                f"✅ {new_notifications} neue Benachrichtigungen wurden nachträglich versendet."
            )
        else:
            response_lines.append("ℹ️ Alle gefundenen Artikel sind entweder bereits bekannt oder zu alt.")

        response_lines.append("💡 Tipp: Verwenden Sie <code>/list</code> für Überwachungsstatus")

        response_text = "\n".join(response_lines)
        
        await status_msg.edit_text(response_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(response_text), "preview": response_text[:120].replace("\n", "⏎")})
        
    except Exception as e:
        logger.error(f"Error in manual backfill check command: {e}")
        error_text = f"❌ Fehler bei der manuellen Verifikation: {str(e)[:200]}"
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
        text = f"❓ Verwenden Sie {code('/admin clear')} zum Bereinigen der Datenbank."
        await message.answer(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
        return
    
    user = await ensure_user(message.from_user)
    
    # Create confirmation keyboard
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ja, alles löschen", callback_data="admin_clear_confirm"),
            InlineKeyboardButton(text="❌ Abbrechen", callback_data="admin_clear_cancel")
        ]
    ])
    
    confirm_text = br_join([
        "⚠️ Achtung: Dies löscht alle gespeicherten Angebote und Benachrichtigungen für alle Nutzer.",
        "Nutzer & Keywords bleiben erhalten. Fortfahren?"
    ])
    await message.answer(confirm_text, reply_markup=keyboard, parse_mode="HTML")
    logger.info({"event": "send_text", "len": len(confirm_text), "preview": confirm_text[:120].replace("\n", "⏎")})

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
        
        now_utc = utc_now()
        
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
            
            # Build keyword entry with proper formatting including poll telemetry
            keyword_lines = [
                f"📝 {b(keyword.original_keyword)}",
                f"Status: {status} — {reason}",
                f"Letzte Prüfung: {fmt_ts_de(keyword.last_checked)} — Letzter Erfolg: {fmt_ts_de(keyword.last_success_ts)}",
                f"Baseline: {keyword.baseline_status}",
                f"Plattformen: {', '.join(keyword.platforms)}"
            ]
            
            # Add poll telemetry if available
            poll_mode_value = getattr(keyword, 'poll_mode', 'full') or 'full'
            mode_labels = {
                "full": "full (Alle Seiten)",
                "rotate": "rotate (rotierendes Fenster, deaktiviert)",
            }
            poll_info_parts = [f"Modus: {mode_labels.get(poll_mode_value, poll_mode_value)}"]

            if hasattr(keyword, 'total_pages_estimate') and keyword.total_pages_estimate:
                poll_info_parts.append(f"Seiten: ~{keyword.total_pages_estimate}")

            if poll_mode_value == "rotate" and hasattr(keyword, 'poll_cursor_page'):
                cursor_page = getattr(keyword, 'poll_cursor_page', 1)
                window_size = getattr(keyword, 'poll_window', 5)
                poll_info_parts.append(f"Fenster: {cursor_page}-{cursor_page + window_size - 1}")

            if hasattr(keyword, 'last_deep_scan_at') and keyword.last_deep_scan_at:
                poll_info_parts.append(f"Tiefe Suche: {fmt_ts_de(keyword.last_deep_scan_at)}")

            keyword_lines.append(f"Poll: {' — '.join(poll_info_parts)}")
            
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
            await callback.message.reply(diagnosis_report, parse_mode="HTML")
            logger.info({"event": "send_text", "len": len(diagnosis_report), "preview": diagnosis_report[:120].replace("\n", "⏎")})
            
        except Exception as e:
            logger.error(f"Error in diagnosis: {e}")
            error_msg = str(e)[:100] + "..." if len(str(e)) > 100 else str(e)
            error_text = f"❌ Fehler bei der Diagnose: {error_msg}"
            await callback.message.reply(error_text, parse_mode="HTML")
            logger.info({"event": "send_text", "len": len(error_text), "preview": error_text[:120].replace("\n", "⏎")})
        
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
        text = "Sie haben derzeit keine Suchbegriffe."
        await callback.message.edit_text(text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
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

    result_text = br_join([
        "🧹 Bereinigung abgeschlossen.",
        "",
        f"• Keywords: {n_kw}",
        f"• Gestoppte Jobs: {stopped}",
        f"• Keyword-Treffer: {n_hits}",
        f"• Benachrichtigungen: {n_notifs}"
    ])
    await callback.message.edit_text(result_text, parse_mode="HTML")
    logger.info({"event": "send_text", "len": len(result_text), "preview": result_text[:120].replace("\n", "⏎")})
    await callback.answer()

async def clear_data_confirm(callback: CallbackQuery):
    """Handle global data wipe confirmation"""
    # Call your existing global wipe function (listings/notifications/hits)
    try:
        res = await db_manager.admin_clear_products()  # existing method
        result_text = br_join([
            "🧹 Bereinigung abgeschlossen.",
            "",
            f"• Listings: {res.get('listings', 0)}",
            f"• Keyword-Treffer: {res.get('keyword_hits', 0)}",
            f"• Benachrichtigungen: {res.get('notifications', 0)}"
        ])
        await callback.message.edit_text(result_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(result_text), "preview": result_text[:120].replace("\n", "⏎")})
    except Exception as e:
        error_text = f"❌ Fehler beim Löschen: {str(e)[:200]}"
        await callback.message.edit_text(error_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(error_text), "preview": error_text[:120].replace("\n", "⏎")})
    await callback.answer()

async def clear_cancel(callback: CallbackQuery):
    """Handle clear operation cancellation"""
    text = "❌ Abgebrochen."
    await callback.message.edit_text(text, parse_mode="HTML")
    logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
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
        success_text = br_join([
            "🧹 Bereinigung abgeschlossen.",
            "",
            f"• Listings: {result['listings']}",
            f"• Keyword-Treffer: {result['keyword_hits']}",
            f"• Benachrichtigungen: {result['notifications']}"
        ])
        await callback.message.edit_text(success_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(success_text), "preview": success_text[:120].replace("\n", "⏎")})
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in admin clear confirm: {e}")
        error_text = "❌ Fehler bei der Bereinigung aufgetreten."
        await callback.message.edit_text(error_text, parse_mode="HTML")
        logger.info({"event": "send_text", "len": len(error_text), "preview": error_text[:120].replace("\n", "⏎")})
        await callback.answer()

async def admin_clear_cancel(callback: CallbackQuery):
    """Handle admin clear cancellation"""
    text = "❌ Abgebrochen."
    await callback.message.edit_text(text, parse_mode="HTML")
    logger.info({"event": "send_text", "len": len(text), "preview": text[:120].replace("\n", "⏎")})
    await callback.answer()

async def cmd_hilfe(message: Message):
    """Handle /hilfe command - show comprehensive help"""
    user = await ensure_user(message.from_user)
    
    help_text = br_join([
        f"🤖 {b('Article Hunter Bot - Hilfe')}",
        "",
        "Dieser Bot überwacht militaria321.com und egun.de nach neuen Angeboten, die zu Ihren Suchbegriffen passen, und benachrichtigt Sie sofort.",
        "",
        f"📋 {b('Verfügbare Befehle:')}",
        "",
        f"🔍 {code('/search <suchbegriff>')}",
        "Neue Überwachung einrichten. Der Bot durchsucht alle Seiten, speichert vorhandene Artikel und startet dann die 60-Sekunden-Überwachung mit Deep-Pagination.",
        f"Beispiel: {code('/search Wehrmacht Helm')}",
        "",
        f"📋 {code('/list')}",
        "Zeigt alle aktiven Überwachungen mit Gesundheitsstatus, Seitenzahlen und Deep-Pagination-Telemetrie an.",
        "",
        f"🔄 {code('/check <suchbegriff>')}",
        "Manuelle Verifikation und Backfill durchführen. Erkennt verpasste Artikel (z.B. wenn Bot offline war) und benachrichtigt nachträglich über neue Funde.",
        f"Beispiel: {code('/check Wehrmacht Helm')}",
        "",
        f"🗑️ {code('/delete <suchbegriff>')}",
        "Überwachung für einen Suchbegriff beenden und aus der Datenbank entfernen.",
        f"Beispiel: {code('/delete Wehrmacht Helm')}",
        "",
        f"🧹 {code('/clear')}",
        "Alle Ihre Suchbegriffe löschen (mit Sicherheitsabfrage).",
        "",
        f"❓ {code('/hilfe')}",
        "Diese Hilfe anzeigen.",
        "",
        f"⚙️ {b('Deep-Pagination System:')}",
        "",
        "Der Bot löst das Problem, dass militaria321.com nach Auktionsende sortiert und neue Artikel auf hinteren Seiten erscheinen können:",
        "",
        f"• {b('Vollständiger Modus (Standard):')} Scannt alle Seiten bei jedem Durchlauf",
        f"• {b('Legacy-Rotationsmodus:')} Deaktiviert – bestehende Überwachungen werden automatisch migriert",
        f"• {b('Intelligente Abdeckung:')} Garantiert, dass keine neuen Artikel übersehen werden",
        f"• {b('Server-freundlich:')} Kontrollierte Anfragen mit Pausen zwischen Seiten",
        "",
        f"📊 {b('Benachrichtigungslogik:')}",
        "",
        "Sie erhalten nur Benachrichtigungen für wirklich NEUE Artikel:",
        f"• Artikel muss {b('nach')} der Überwachungszeit inseriert worden sein",
        f"• Artikel darf noch {b('nicht gesehen')} worden sein",
        f"• {b('60-Minuten Kulanzfenster')} für Artikel ohne Zeitstempel",
        "",
        f"🌍 {b('Zeitzone:')} Alle Zeiten in Deutschland (Europe/Berlin)",
        f"🔄 {b('Frequenz:')} Überwachung alle 60 Sekunden",
        f"📱 {b('Plattformen:')} {', '.join(search_service.providers.keys())}",
        "",
        f"💡 {b('Tipps:')}",
        f"• Verwenden Sie {code('/list')}, um den Status Ihrer Überwachungen zu prüfen",
        f"• Mit {code('/check')} können Sie manuell nach neuen Artikeln suchen",
        "• Der Bot zeigt die Gesundheit jeder Überwachung an",
        f"• Bei Problemen nutzen Sie die 🔍 Diagnostik-Funktion in {code('/list')}",
        "",
        f"🎯 {b('Developed by:')} Deep-Pagination Experte",
        f"📚 {b('Version:')} 2.0 mit Deep-Pagination Support"
    ])
    
    await message.answer(help_text, parse_mode="HTML")
    logger.info({"event": "send_text", "len": len(help_text), "preview": help_text[:120].replace("\n", "⏎")})

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
    search_service.attach_notification_service(notification_service)
    
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
    router.message.register(cmd_hilfe, Command("hilfe"))  # Help command
    
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