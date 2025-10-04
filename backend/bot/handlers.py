from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import logging
from datetime import datetime
from typing import List

from models import User, Keyword
from services.keyword_service import KeywordService

logger = logging.getLogger(__name__)

# Create router
router = Router()

# Services will be injected from main application
db_manager = None
keyword_service = None

def set_services(db_mgr, keyword_svc):
    """Set services from main application"""
    global db_manager, keyword_service
    db_manager = db_mgr
    keyword_service = keyword_svc


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Handle /start command"""
    user = await ensure_user(message.from_user)
    
    welcome_text = """🎖️ **Willkommen zum Militaria Auktions-Bot!**

Dieser Bot durchsucht kontinuierlich Militaria321.com nach Ihren Suchbegriffen und sendet sofortige Benachrichtigungen bei neuen Treffern.

**Verfügbare Befehle:**
/suche <Begriff> - Neuen Suchbegriff hinzufügen
/liste - Ihre aktiven Suchbegriffe anzeigen
/hilfe - Alle Befehle anzeigen

**Beispiel:**
`/suche Wehrmacht Helm`

Starten Sie jetzt mit Ihrem ersten Suchbegriff! 🔍"""

    await message.answer(welcome_text, parse_mode="Markdown")


@router.message(Command("hilfe"))
async def cmd_help(message: Message):
    """Handle /hilfe command"""
    help_text = """📋 **Befehlsübersicht:**

**Suchbegriffe verwalten:** *(alle Befehle sind groß-/kleinschreibungsunabhängig)*
/suche <Begriff> - Neuen Suchbegriff erstellen (zeigt sofort erste Treffer)
/liste - Aktive Suchbegriffe anzeigen  
/testen <Begriff> - Aktuelle Treffer für Begriff anzeigen
/aendern <Alt> <Neu> - Suchbegriff umbenennen
/loeschen <Begriff> - Suchbegriff löschen (mit Bestätigung)

**Einstellungen:**
/pausieren <Begriff> - Suchbegriff pausieren
/fortsetzen <Begriff> - Suchbegriff fortsetzen
/frequenz <Begriff> <Zeit> - Suchfrequenz ändern (60s, 5m, 15m)
/stumm <Begriff> [Dauer] - Benachrichtigungen stummschalten
/laut <Begriff> - Stummschaltung aufheben

**Verwaltung:**
/export - Suchbegriffe als Datei exportieren

**Beispiele:**
`/suche "Wehrmacht Helm"` - Erstellt Begriff und zeigt echte Treffer oder "keine Treffer"
`/testen "kappmesser"` - Zeigt aktuelle Treffer (groß-/kleinschreibungsunabhängig)
`/pausieren "HELM"` - Funktioniert auch mit Großbuchstaben
`/stumm "Wehrmacht Helm" 30m`

**Plattform:** Militaria321.com
**Hinweis:** Alle Befehle arbeiten mit exakter Titel-Übereinstimmung und deutscher Preisformatierung."""

    await message.answer(help_text, parse_mode="Markdown")


@router.message(Command("suche"))
async def cmd_search(message: Message):
    """Handle /suche command"""
    user = await ensure_user(message.from_user)
    
    # Extract keyword from command
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer("❌ Bitte geben Sie einen Suchbegriff an.\n\nBeispiel: `/suche Wehrmacht Helm`", parse_mode="Markdown")
        return
    
    keyword_text = args[1].strip()
    if not keyword_text:
        await message.answer("❌ Suchbegriff darf nicht leer sein.")
        return
    
    if len(keyword_text) > 100:
        await message.answer("❌ Suchbegriff ist zu lang (max. 100 Zeichen).")
        return
    
    # Check if keyword already exists (case-insensitive)
    existing = await keyword_service.get_user_keyword(user.id, keyword_text)
    if existing:
        await message.answer(f"⚠️ Suchbegriff **'{existing.keyword}'** existiert bereits (gefunden als: {keyword_text}).", parse_mode="Markdown")
        return
    
    # Show "searching" message
    searching_msg = await message.answer("🔍 **Suche läuft...**\n\nSuche erste Treffer für Ihren Begriff.", parse_mode="Markdown")
    
    # Create new keyword or reset existing one
    try:
        keyword = await keyword_service.create_keyword(user.id, keyword_text)
        
        # Perform setup search with count reporting
        await perform_setup_search_with_count(message, keyword, keyword_text, searching_msg)
        
    except Exception as e:
        logger.error(f"Error creating keyword: {e}")
        await searching_msg.edit_text("❌ Fehler beim Erstellen des Suchbegriffs. Bitte versuchen Sie es erneut.")


async def perform_setup_search_with_count(message: Message, keyword, keyword_text: str, searching_msg: Message):
    """Perform full baseline seeding across ALL pages for all providers"""
    try:
        from services.search_service import SearchService
        from datetime import datetime
        
        # Reset keyword subscription
        await keyword_service.reset_keyword_subscription(keyword.id)
        
        # Perform full baseline seeding (crawls ALL pages)
        search_service = SearchService(db_manager)
        
        # Update status message
        await searching_msg.edit_text(f"🔍 **Baseline wird erstellt...**\n\nDurchsuche alle Seiten für \"{keyword_text}\" – dies kann einige Sekunden dauern.", parse_mode="Markdown")
        
        seeding_results = await search_service.full_baseline_seed(
            keyword_text=keyword_text,
            keyword_id=keyword.id,
            user_id=keyword.user_id
        )
        
        # Check baseline status
        keyword_updated = await keyword_service.get_keyword_by_id(keyword.id)
        baseline_status = keyword_updated.baseline_status if keyword_updated else "unknown"
        
        # Build confirmation header
        setup_text = f"**Suche eingerichtet: \"{keyword_text}\"**\n\n"
        
        # Add results per provider (deterministic alphabetical order)
        total_items_seeded = 0
        failed_platforms = []
        
        for platform in sorted(seeding_results.keys()):
            result = seeding_results[platform]
            
            if result["error"]:
                count_text = f"(Fehler: {result['error']})"
                failed_platforms.append(platform)
            else:
                count_text = f"{result['items_collected']} Treffer gefunden ({result['pages_scanned']} Seiten durchsucht)"
                total_items_seeded += result["items_collected"]
            
            # Format platform name
            platform_display = platform.replace(".com", "").replace(".de", "").capitalize()
            setup_text += f"• **{platform_display}**: {count_text}\n"
        
        # Add placeholder for future platforms
        setup_text += "• **Weitere Plattformen**: in Vorbereitung\n\n"
        
        # Add status-specific summary
        if baseline_status == "complete":
            setup_text += f"✅ **Baseline vollständig**: {total_items_seeded} Angebote erfasst\n"
            setup_text += "Ich benachrichtige Sie künftig nur bei neuen Angeboten.\n\n"
        elif baseline_status == "partial":
            setup_text += f"⚠️ **Baseline teilweise erstellt**: {total_items_seeded} Angebote erfasst\n"
            setup_text += f"Fehler bei: {', '.join(failed_platforms)}\n\n"
        elif baseline_status == "error":
            setup_text += "❌ **Baseline-Erstellung fehlgeschlagen**\n"
            setup_text += "Bitte versuchen Sie es erneut.\n\n"
        
        setup_text += f"⏱️ Frequenz: Alle 60 Sekunden\n"
        setup_text += f"🔍 Verwenden Sie `/testen {keyword_text}` um Beispielergebnisse zu sehen."
        
        # Mark first run completed with current timestamp
        await keyword_service.mark_first_run_completed(keyword.id, datetime.utcnow())
        
        # Create inline keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Statistiken", callback_data=f"stats_{keyword.id}"),
                InlineKeyboardButton(text="🧪 Testen", callback_data=f"test_{keyword.id}")
            ],
            [
                InlineKeyboardButton(text="⏸️ Pausieren", callback_data=f"pause_{keyword.id}"),
                InlineKeyboardButton(text="🗑️ Löschen", callback_data=f"delete_{keyword.id}")
            ]
        ])
        
        # Edit the searching message with results
        await searching_msg.edit_text(setup_text, parse_mode="Markdown", reply_markup=keyboard)
        
        logger.info(f"Full baseline seed for '{keyword_text}': {total_items_seeded} items seeded")
        
    except Exception as e:
        logger.error(f"Error performing setup search: {e}")
        await searching_msg.edit_text(f"❌ Fehler beim Einrichten der Suche für **'{keyword_text}'**.\n\nBitte versuchen Sie es erneut.", parse_mode="Markdown")


# Removed mark_sample_items_as_seen - now using seen_set approach


@router.message(Command("liste"))
async def cmd_list(message: Message):
    """Handle /liste command"""
    user = await ensure_user(message.from_user)
    
    keywords = await keyword_service.get_user_keywords(user.id)
    
    if not keywords:
        await message.answer("📝 Sie haben noch keine Suchbegriffe erstellt.\n\nVerwenden Sie `/suche <Begriff>` um zu beginnen.", parse_mode="Markdown")
        return
    
    text = "📋 **Ihre Suchbegriffe:**\n\n"
    
    for keyword in keywords:
        status_emoji = "✅" if keyword.is_active else "⏸️"
        mute_emoji = "🔇" if keyword.is_muted else ""
        
        freq_text = f"{keyword.frequency_seconds}s"
        if keyword.frequency_seconds >= 60:
            freq_text = f"{keyword.frequency_seconds // 60}m"
        
        last_check = "Nie"
        if keyword.last_checked:
            last_check = keyword.last_checked.strftime("%d.%m. %H:%M")
        
        text += f"{status_emoji} **{keyword.keyword}** {mute_emoji}\n"
        text += f"   📊 Frequenz: {freq_text} | 🕐 Letzter Check: {last_check}\n\n"
    
    # Add management buttons
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Neuer Begriff", callback_data="new_keyword"),
            InlineKeyboardButton(text="📤 Exportieren", callback_data="export_keywords")
        ]
    ])
    
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)


@router.message(Command("loeschen"))
async def cmd_delete(message: Message):
    """Handle /loeschen command - re-enabled with confirmation"""
    user = await ensure_user(message.from_user)
    
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer("❌ Bitte geben Sie den zu löschenden Suchbegriff an.\n\nBeispiel: `/loeschen Wehrmacht Helm`", parse_mode="Markdown")
        return
    
    keyword_text = args[1].strip()
    if not keyword_text:
        await message.answer("❌ Suchbegriff darf nicht leer sein.")
        return
    
    # Find keyword (case-insensitive)
    keyword = await keyword_service.get_user_keyword(user.id, keyword_text)
    
    if not keyword:
        await message.answer(f"❌ Suchbegriff **'{keyword_text}'** nicht gefunden.", parse_mode="Markdown")
        return
    
    # Show confirmation dialog
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ja, löschen", callback_data=f"confirm_delete_{keyword.id}"),
            InlineKeyboardButton(text="❌ Abbrechen", callback_data="cancel_delete")
        ]
    ])
    
    await message.answer(
        f"⚠️ **Suchbegriff löschen?**\n\n🔍 Begriff: **{keyword.keyword}**\n\n📊 Status: {'Aktiv' if keyword.is_active else 'Pausiert'}\n⏱️ Frequenz: {keyword.frequency_seconds}s\n\n**Diese Aktion kann nicht rückgängig gemacht werden.**",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.message(Command("pausieren"))
async def cmd_pause(message: Message):
    """Handle /pausieren command (case-insensitive)"""
    user = await ensure_user(message.from_user)
    
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer("❌ Bitte geben Sie den zu pausierenden Suchbegriff an.\n\nBeispiel: `/pausieren Wehrmacht Helm`", parse_mode="Markdown")
        return
    
    keyword_text = args[1].strip()
    keyword = await keyword_service.get_user_keyword(user.id, keyword_text)
    
    if not keyword:
        await message.answer(f"❌ Suchbegriff **'{keyword_text}'** nicht gefunden.", parse_mode="Markdown")
        return
    
    if not keyword.is_active:
        await message.answer(f"⚠️ Suchbegriff **'{keyword.keyword}'** ist bereits pausiert.", parse_mode="Markdown")
        return
    
    # Pause keyword
    await keyword_service.update_keyword_status(keyword.id, is_active=False)
    
    await message.answer(f"⏸️ Suchbegriff **'{keyword.keyword}'** wurde pausiert.\n\nVerwenden Sie `/fortsetzen {keyword.keyword}` um fortzufahren.", parse_mode="Markdown")


@router.message(Command("fortsetzen"))
async def cmd_resume(message: Message):
    """Handle /fortsetzen command (case-insensitive)"""
    user = await ensure_user(message.from_user)
    
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer("❌ Bitte geben Sie den fortzusetzenden Suchbegriff an.\n\nBeispiel: `/fortsetzen Wehrmacht Helm`", parse_mode="Markdown")
        return
    
    keyword_text = args[1].strip()
    keyword = await keyword_service.get_user_keyword(user.id, keyword_text)
    
    if not keyword:
        await message.answer(f"❌ Suchbegriff **'{keyword_text}'** nicht gefunden.", parse_mode="Markdown")
        return
    
    if keyword.is_active:
        await message.answer(f"⚠️ Suchbegriff **'{keyword.keyword}'** ist bereits aktiv.", parse_mode="Markdown")
        return
    
    # Resume keyword
    await keyword_service.update_keyword_status(keyword.id, is_active=True)
    
    await message.answer(f"▶️ Suchbegriff **'{keyword.keyword}'** wurde fortgesetzt.\n\nDie Suche läuft wieder.", parse_mode="Markdown")


@router.message(Command("testen"))
async def cmd_test(message: Message):
    """Handle /testen command - show sample blocks per provider (case-insensitive, provider-agnostic)"""
    user = await ensure_user(message.from_user)
    
    # Parse arguments: /testen <keyword> [provider_filter]
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Bitte geben Sie den zu testenden Suchbegriff an.\n\nBeispiel: `/testen Pistole`\n\nOptional mit Filter: `/testen Pistole egun`", parse_mode="Markdown")
        return
    
    keyword_text = " ".join(args[1:-1]) if len(args) > 2 and args[-1] in ["egun", "militaria"] else " ".join(args[1:])
    provider_filter = args[-1] if len(args) > 2 and args[-1] in ["egun", "militaria"] else None
    
    # Map short names to full platform names
    if provider_filter:
        if provider_filter == "egun":
            provider_filter = ["egun.de"]
        elif provider_filter == "militaria":
            provider_filter = ["militaria321.com"]
    
    keyword = await keyword_service.get_user_keyword(user.id, keyword_text)
    
    if not keyword:
        await message.answer(f"❌ Suchbegriff **'{keyword_text}'** nicht gefunden.\n\nVerwenden Sie `/suche {keyword_text}` um ihn anzulegen.", parse_mode="Markdown")
        return
    
    # Show "searching" message
    searching_msg = await message.answer("🔍 **Test läuft...**\n\nSuche aktuelle Treffer für Ihren Begriff.", parse_mode="Markdown")
    
    # Perform sample search (without changing baseline)
    try:
        from services.search_service import SearchService
        from decimal import Decimal
        
        search_service = SearchService(db_manager)
        sample_blocks = await search_service.get_sample_blocks(keyword_text, providers_filter=provider_filter, seed_baseline=False)
        
        # Build response with per-provider blocks
        test_text = f"**Erster Trefferblock für \"{keyword_text}\"**\n"
        test_text += "(Testansicht – keine Änderungen an der Überwachung)\n\n"
        
        # Render blocks in deterministic order (alphabetical)
        for platform in sorted(sample_blocks.keys()):
            block = sample_blocks[platform]
            provider = block["provider"]
            
            test_text += f"**Erste Treffer – {platform}**\n"
            
            if block["error"]:
                test_text += f"(Fehler: {block['error']})\n\n"
                continue
            
            if not block["matched_items"]:
                test_text += "(keine Treffer gefunden)\n\n"
                continue
            
            # Show exactly top 3 items
            for i, item in enumerate(block["matched_items"], 1):
                # Format price
                if item.price_value:
                    price_formatted = provider.format_price_de(
                        Decimal(str(item.price_value)),
                        item.price_currency or "EUR"
                    )
                else:
                    price_formatted = "N/A"
                
                test_text += f"{i}) {item.title} – {price_formatted} – {item.url}\n"
            
            # Add suffix line
            if block["total_count"] and block["total_count"] > 3:
                more_count = block["total_count"] - 3
                test_text += f"({more_count} weitere Treffer)\n"
            elif block["has_more"]:
                test_text += "(weitere Treffer verfügbar)\n"
            
            test_text += "\n"
        
        test_text += f"🔍 Begriff: **{keyword.keyword}** (aktiv überwacht)"
        
        # Create inline keyboard with refresh buttons per provider
        keyboard_rows = []
        for platform in sorted(sample_blocks.keys()):
            platform_short = platform.split(".")[0]  # "egun" or "militaria321"
            keyboard_rows.append([
                InlineKeyboardButton(text=f"🔄 Aktualisieren – {platform}", callback_data=f"retest:{platform_short}:{keyword.id}")
            ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        
        await searching_msg.edit_text(test_text, parse_mode="Markdown", reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error performing test search: {e}")
        await searching_msg.edit_text(f"❌ Fehler beim Testen von **'{keyword_text}'**.", parse_mode="Markdown")


async def ensure_user(telegram_user) -> User:
    """Ensure user exists in database"""
    if not db_manager:
        logger.error("Database manager not initialized")
        raise Exception("Database not available")
    
    user = await db_manager.get_user_by_telegram_id(telegram_user.id)
    
    if not user:
        user_data = User(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name
        )
        await db_manager.create_user(user_data)
        user = user_data
    
    return user