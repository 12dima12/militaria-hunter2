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
    """Perform setup search across all providers, render per-provider blocks, and seed seen_set"""
    try:
        from providers import get_all_providers
        from decimal import Decimal
        
        # Reset keyword subscription
        await keyword_service.reset_keyword_subscription(keyword.id)
        
        # Get all providers in deterministic order (alphabetical)
        providers = get_all_providers()
        
        # Build confirmation header
        setup_text = f"**Suche eingerichtet: \"{keyword_text}\"**\n\n"
        setup_text += "Ich melde zunächst den aktuellen Stand und benachrichtige Sie künftig nur bei neuen Angeboten.\n\n"
        
        # Search each provider and build per-provider blocks
        all_matched_items = []
        grand_total = 0
        
        for provider in providers:
            try:
                # Search with comprehensive results
                search_result = await provider.search(keyword_text, sample_mode=True)
                
                # Apply title-only matching to get accurate count
                matched_items = []
                for item in search_result.items:
                    if provider.matches_keyword(item.title, keyword_text):
                        matched_items.append(item)
                
                matched_count = len(matched_items)
                grand_total += matched_count
                all_matched_items.extend(matched_items)
                
                # Build per-provider block
                setup_text += f"**Erste Treffer – {provider.name}**\n"
                
                if matched_count > 0:
                    # Show exactly top 3 items
                    for i, item in enumerate(matched_items[:3], 1):
                        # Format price
                        if item.price_value:
                            price_formatted = provider.format_price_de(
                                Decimal(str(item.price_value)),
                                item.price_currency or "EUR"
                            )
                        else:
                            price_formatted = "N/A"
                        
                        setup_text += f"{i}) {item.title} – {price_formatted} – {item.url}\n"
                    
                    # Add suffix line
                    if search_result.total_count and search_result.total_count > 3:
                        more_count = search_result.total_count - 3
                        setup_text += f"({more_count} weitere Treffer)\n"
                    elif search_result.has_more:
                        setup_text += "(weitere Treffer verfügbar)\n"
                else:
                    # Zero results
                    setup_text += "(keine Treffer gefunden)\n"
                
                setup_text += "\n"
                
                logger.info(f"Provider {provider.name}: {matched_count} matches for '{keyword_text}'")
                
            except Exception as e:
                logger.error(f"Error searching provider {provider.name}: {e}")
                setup_text += f"**Erste Treffer – {provider.name}**\n"
                setup_text += "(Fehler beim Abrufen der Ergebnisse)\n\n"
        
        # Add global summary if all providers returned zero
        if grand_total == 0:
            setup_text += f"Für \"{keyword_text}\" wurden aktuell keine Treffer gefunden. Ich überwache weiter und benachrichtige Sie bei neuen Angeboten.\n\n"
        
        # Add management info
        setup_text += f"⏱️ Frequenz: Alle 60 Sekunden\n"
        setup_text += f"🌐 Plattformen: {', '.join([p.name for p in providers])}"
        
        # Seed the seen_set with all current matches to prevent immediate notifications
        await keyword_service.seed_seen_set(keyword.id, all_matched_items)
        
        # Create inline keyboard
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Statistiken", callback_data=f"stats_{keyword.id}"),
                InlineKeyboardButton(text="⏸️ Pausieren", callback_data=f"pause_{keyword.id}")
            ],
            [
                InlineKeyboardButton(text="🔄 Aktuellen Stand zeigen", callback_data=f"show_current_{keyword.id}"),
                InlineKeyboardButton(text="🗑️ Löschen", callback_data=f"delete_{keyword.id}")
            ]
        ])
        
        # Edit the searching message with results
        await searching_msg.edit_text(setup_text, parse_mode="Markdown", reply_markup=keyboard)
        
        logger.info(f"Setup search for '{keyword_text}': {grand_total} total matches across {len(providers)} providers, seeded seen_set")
        
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
    """Handle /testen command - re-run sample search (case-insensitive)"""
    user = await ensure_user(message.from_user)
    
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer("❌ Bitte geben Sie den zu testenden Suchbegriff an.\n\nBeispiel: `/testen Wehrmacht Helm`", parse_mode="Markdown")
        return
    
    keyword_text = args[1].strip()
    keyword = await keyword_service.get_user_keyword(user.id, keyword_text)
    
    if not keyword:
        await message.answer(f"❌ Suchbegriff **'{keyword_text}'** nicht gefunden.", parse_mode="Markdown")
        return
    
    # Show "searching" message
    searching_msg = await message.answer("🔍 **Test läuft...**\n\nSuche aktuelle Treffer für Ihren Begriff.", parse_mode="Markdown")
    
    # Perform sample search (without marking as seen)
    try:
        from providers.militaria321 import Militaria321Provider
        
        provider = Militaria321Provider()
        search_result = await provider.search(keyword_text, sample_mode=True)
        
        if search_result.items:
            # Show top 3 results
            sample_text = f"**Test-Ergebnisse – militaria321.com**\n\n"
            
            shown_count = min(3, len(search_result.items))
            for i in range(shown_count):
                item = search_result.items[i]
                
                # Format price using German locale
                price_str = ""
                if item.price_value and item.price_currency:
                    from decimal import Decimal
                    from providers.militaria321 import Militaria321Provider
                    
                    provider = Militaria321Provider()
                    formatted_price = provider.format_price_de(Decimal(str(item.price_value)), item.price_currency)
                    price_str = f" – {formatted_price}"
                elif item.price_value:
                    from decimal import Decimal
                    from providers.militaria321 import Militaria321Provider
                    
                    provider = Militaria321Provider()
                    formatted_price = provider.format_price_de(Decimal(str(item.price_value)), "EUR")
                    price_str = f" – {formatted_price}"
                
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
            sample_text = f"**Test-Ergebnisse – militaria321.com**\n\n*(keine Treffer gefunden)*"
        
        sample_text += f"\n\n🔍 Begriff: **{keyword.keyword}** (aktiv überwacht)"
        
        await searching_msg.edit_text(sample_text, parse_mode="Markdown")
        
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