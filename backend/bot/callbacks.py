from aiogram import Router, types
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
import logging
from datetime import datetime

from services.keyword_service import KeywordService

logger = logging.getLogger(__name__)

# Create router for callbacks
callback_router = Router()

# Services will be injected from main application
db_manager = None
keyword_service = None

def set_services(db_mgr, keyword_svc):
    """Set services from main application"""
    global db_manager, keyword_service
    db_manager = db_mgr
    keyword_service = keyword_svc


@callback_router.callback_query(lambda c: c.data.startswith("confirm_delete_"))
async def callback_confirm_delete(callback_query: CallbackQuery):
    """Handle delete confirmation - re-enabled"""
    await callback_query.answer()
    
    keyword_id = callback_query.data.split("_")[-1]
    
    try:
        keyword = await keyword_service.get_keyword_by_id(keyword_id)
        if not keyword:
            await callback_query.message.edit_text("❌ Suchbegriff nicht gefunden.")
            return
        
        # Check ownership
        user = await db_manager.get_user_by_telegram_id(callback_query.from_user.id)
        if not user or keyword.user_id != user.id:
            await callback_query.message.edit_text("❌ Keine Berechtigung.")
            return
        
        # Delete keyword
        success = await keyword_service.delete_keyword(keyword_id)
        
        if success:
            await callback_query.message.edit_text(
                f"✅ **Suchbegriff gelöscht**\n\n🔍 Begriff: **'{keyword.keyword}'** wurde erfolgreich entfernt.\n\nSie erhalten keine weiteren Benachrichtigungen für diesen Begriff.",
                parse_mode="Markdown"
            )
            logger.info(f"Keyword '{keyword.keyword}' deleted by user {user.telegram_id}")
        else:
            await callback_query.message.edit_text("❌ Fehler beim Löschen des Suchbegriffs.")
        
    except Exception as e:
        logger.error(f"Error deleting keyword: {e}")
        await callback_query.message.edit_text("❌ Fehler beim Löschen des Suchbegriffs.")


@callback_router.callback_query(lambda c: c.data == "cancel_delete")
async def callback_cancel_delete(callback_query: CallbackQuery):
    """Handle delete cancellation"""
    await callback_query.answer("Abgebrochen")
    await callback_query.message.edit_text("❌ Löschvorgang abgebrochen.")


@callback_router.callback_query(lambda c: c.data.startswith("pause_"))
async def callback_pause_keyword(callback_query: CallbackQuery):
    """Handle keyword pause"""
    await callback_query.answer()
    
    keyword_id = callback_query.data.split("_")[-1]
    
    try:
        keyword = await keyword_service.get_keyword_by_id(keyword_id)
        if not keyword:
            await callback_query.message.edit_text("❌ Suchbegriff nicht gefunden.")
            return
        
        # Check ownership
        user = await db_manager.get_user_by_telegram_id(callback_query.from_user.id)
        if not user or keyword.user_id != user.id:
            await callback_query.answer("❌ Keine Berechtigung", show_alert=True)
            return
        
        # Toggle pause status
        new_status = not keyword.is_active
        await keyword_service.update_keyword_status(keyword_id, is_active=new_status)
        
        status_text = "fortgesetzt" if new_status else "pausiert"
        status_emoji = "▶️" if new_status else "⏸️"
        
        # Update keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Statistiken", callback_data=f"stats_{keyword.id}"),
                InlineKeyboardButton(
                    text="▶️ Fortsetzen" if not new_status else "⏸️ Pausieren", 
                    callback_data=f"pause_{keyword.id}"
                )
            ],
            [
                InlineKeyboardButton(text="🔄 Erneut testen", callback_data=f"retest_{keyword.id}"),
                InlineKeyboardButton(text="🗑️ Löschen", callback_data=f"delete_{keyword.id}")
            ]
        ])
        
        await callback_query.message.edit_reply_markup(reply_markup=keyboard)
        await callback_query.answer(f"{status_emoji} {keyword.keyword} {status_text}")
        
    except Exception as e:
        logger.error(f"Error pausing keyword: {e}")
        await callback_query.answer("❌ Fehler", show_alert=True)


@callback_router.callback_query(lambda c: c.data.startswith("stats_"))
async def callback_keyword_stats(callback_query: CallbackQuery):
    """Show keyword statistics"""
    await callback_query.answer()
    
    keyword_id = callback_query.data.split("_")[-1]
    
    try:
        keyword = await keyword_service.get_keyword_by_id(keyword_id)
        if not keyword:
            await callback_query.answer("❌ Suchbegriff nicht gefunden", show_alert=True)
            return
        
        # Check ownership
        user = await db_manager.get_user_by_telegram_id(callback_query.from_user.id)
        if not user or keyword.user_id != user.id:
            await callback_query.answer("❌ Keine Berechtigung", show_alert=True)
            return
        
        # Get statistics
        total_hits = await keyword_service.get_keyword_hit_count(keyword_id)
        
        freq_text = f"{keyword.frequency_seconds}s"
        if keyword.frequency_seconds >= 60:
            freq_text = f"{keyword.frequency_seconds // 60}m"
        
        last_check = "Nie"
        if keyword.last_checked:
            last_check = keyword.last_checked.strftime("%d.%m.%Y %H:%M")
        
        status = "Aktiv" if keyword.is_active else "Pausiert"
        mute_status = "Stumm" if keyword.is_muted else "Normal"
        
        stats_text = f"""📊 **Statistiken: {keyword.keyword}**

**Status:** {status}
**Benachrichtigungen:** {mute_status}
**Frequenz:** {freq_text}
**Letzte Prüfung:** {last_check}
**Treffer gesamt:** {total_hits}
**Erstellt:** {keyword.created_at.strftime("%d.%m.%Y")}

**Plattformen:** Militaria321.com"""

        await callback_query.message.answer(stats_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error showing stats: {e}")
        await callback_query.answer("❌ Fehler beim Laden der Statistiken", show_alert=True)


@callback_router.callback_query(lambda c: c.data == "new_keyword")
async def callback_new_keyword(callback_query: CallbackQuery):
    """Prompt for new keyword"""
    await callback_query.answer()
    
    await callback_query.message.answer(
        "➕ **Neuen Suchbegriff erstellen**\n\nSenden Sie: `/suche <Ihr Begriff>`\n\nBeispiel: `/suche Wehrmacht Medaille`",
        parse_mode="Markdown"
    )


@callback_router.callback_query(lambda c: c.data == "export_keywords")
async def callback_export_keywords(callback_query: CallbackQuery):
    """Export user keywords"""
    await callback_query.answer()
    
    try:
        user = await db_manager.get_user_by_telegram_id(callback_query.from_user.id)
        if not user:
            await callback_query.answer("❌ Benutzer nicht gefunden", show_alert=True)
            return
        
        keywords = await keyword_service.get_user_keywords(user.id)
        
        if not keywords:
            await callback_query.answer("📝 Keine Suchbegriffe zum Exportieren", show_alert=True)
            return
        
        # Create CSV-like export
        export_text = "# Ihre Suchbegriffe\n\n"
        export_text += f"Exportiert am: {datetime.utcnow().strftime('%d.%m.%Y %H:%M')} UTC\n\n"
        
        for keyword in keywords:
            status = "Aktiv" if keyword.is_active else "Pausiert"
            freq_text = f"{keyword.frequency_seconds}s"
            if keyword.frequency_seconds >= 60:
                freq_text = f"{keyword.frequency_seconds // 60}m"
            
            export_text += f"Begriff: {keyword.keyword}\n"
            export_text += f"Status: {status}\n"
            export_text += f"Frequenz: {freq_text}\n"
            export_text += f"Erstellt: {keyword.created_at.strftime('%d.%m.%Y')}\n"
            export_text += "-" * 30 + "\n"
        
        # Send as file or text based on length
        if len(export_text) > 4000:
            # TODO: Implement file sending
            await callback_query.message.answer("📤 Export zu groß. Feature wird in Kürze verfügbar sein.")
        else:
            await callback_query.message.answer(f"```\n{export_text}\n```", parse_mode="MarkdownV2")
        
    except Exception as e:
        logger.error(f"Error exporting keywords: {e}")
        await callback_query.answer("❌ Fehler beim Exportieren", show_alert=True)


@callback_router.callback_query(lambda c: c.data.startswith("retest_"))
async def callback_retest_keyword(callback_query: CallbackQuery):
    """Handle keyword retest"""
    await callback_query.answer()
    
    keyword_id = callback_query.data.split("_")[-1]
    
    try:
        keyword = await keyword_service.get_keyword_by_id(keyword_id)
        if not keyword:
            await callback_query.message.answer("❌ Suchbegriff nicht gefunden.")
            return
        
        # Check ownership
        user = await db_manager.get_user_by_telegram_id(callback_query.from_user.id)
        if not user or keyword.user_id != user.id:
            await callback_query.answer("❌ Keine Berechtigung", show_alert=True)
            return
        
        # Show "searching" message
        searching_msg = await callback_query.message.answer("🔍 **Erneuter Test läuft...**\n\nSuche aktuelle Treffer.", parse_mode="Markdown")
        
        # Perform sample search
        from providers.militaria321 import Militaria321Provider
        
        provider = Militaria321Provider()
        search_result = await provider.search(keyword.keyword, sample_mode=True)
        
        if search_result.items:
            # Show top 3 results
            sample_text = f"**Aktuelle Treffer – militaria321.com**\n\n"
            
            shown_count = min(3, len(search_result.items))
            for i in range(shown_count):
                item = search_result.items[i]
                
                # Format price
                price_str = ""
                if item.price_value and item.price_currency:
                    price_str = f" – {item.price_value:.2f} {item.price_currency}"
                elif item.price_value:
                    price_str = f" – {item.price_value:.2f} €"
                
                # Format location
                location_str = ""
                if item.location:
                    location_str = f" – {item.location}"
                
                sample_text += f"{i+1}. [{item.title[:60]}...]({item.url}){price_str}{location_str}\n\n"
            
            # Add "more results" line
            remaining = len(search_result.items) - shown_count
            if search_result.total_count and search_result.total_count > shown_count:
                remaining = search_result.total_count - shown_count
                sample_text += f"*({remaining} weitere Treffer)*"
            elif remaining > 0:
                sample_text += f"*({remaining} weitere Treffer)*"
            elif search_result.has_more:
                sample_text += f"*(weitere Treffer verfügbar)*"
        else:
            sample_text = f"**Aktuelle Treffer – militaria321.com**\n\n❌ Keine Treffer für **'{keyword.keyword}'** gefunden."
        
        sample_text += f"\n\n🔍 Begriff: **{keyword.keyword}** (aktiv überwacht)"
        
        await searching_msg.edit_text(sample_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in retest: {e}")
        await callback_query.answer("❌ Fehler beim erneuten Test", show_alert=True)


@callback_router.callback_query(lambda c: c.data.startswith("mute_30m_"))
async def callback_mute_keyword(callback_query: CallbackQuery):
    """Handle keyword mute (30 minutes)"""
    await callback_query.answer()
    
    keyword_id = callback_query.data.split("_")[-1]
    
    try:
        keyword = await keyword_service.get_keyword_by_id(keyword_id)
        if not keyword:
            await callback_query.answer("❌ Suchbegriff nicht gefunden", show_alert=True)
            return
        
        # Check ownership
        user = await db_manager.get_user_by_telegram_id(callback_query.from_user.id)
        if not user or keyword.user_id != user.id:
            await callback_query.answer("❌ Keine Berechtigung", show_alert=True)
            return
        
        # Mute for 30 minutes
        from datetime import timedelta
        mute_until = datetime.utcnow() + timedelta(minutes=30)
        await keyword_service.update_keyword_status(keyword_id, is_muted=True, muted_until=mute_until)
        
        await callback_query.answer("🔇 Für 30 Minuten stummgeschaltet")
        await callback_query.message.answer(
            f"🔇 Suchbegriff **'{keyword.keyword}'** ist für 30 Minuten stummgeschaltet.\n\nVerwenden Sie `/laut {keyword.keyword}` um wieder zu aktivieren.",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error muting keyword: {e}")
        await callback_query.answer("❌ Fehler beim Stummschalten", show_alert=True)


@callback_router.callback_query(lambda c: c.data.startswith("delete_"))
async def callback_delete_keyword(callback_query: CallbackQuery):
    """Handle delete button press from inline keyboard"""
    await callback_query.answer()
    
    keyword_id = callback_query.data.split("_")[-1]
    
    try:
        keyword = await keyword_service.get_keyword_by_id(keyword_id)
        if not keyword:
            await callback_query.answer("❌ Suchbegriff nicht gefunden", show_alert=True)
            return
        
        # Check ownership
        user = await db_manager.get_user_by_telegram_id(callback_query.from_user.id)
        if not user or keyword.user_id != user.id:
            await callback_query.answer("❌ Keine Berechtigung", show_alert=True)
            return
        
        # Show confirmation dialog
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ja, löschen", callback_data=f"confirm_delete_{keyword.id}"),
                InlineKeyboardButton(text="❌ Abbrechen", callback_data="cancel_delete")
            ]
        ])
        
        await callback_query.message.answer(
            f"⚠️ **Suchbegriff löschen?**\n\n🔍 Begriff: **{keyword.keyword}**\n\n📊 Status: {'Aktiv' if keyword.is_active else 'Pausiert'}\n⏱️ Frequenz: {keyword.frequency_seconds}s\n\n**Diese Aktion kann nicht rückgängig gemacht werden.**",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error in delete callback: {e}")
        await callback_query.answer("❌ Fehler beim Löschen", show_alert=True)