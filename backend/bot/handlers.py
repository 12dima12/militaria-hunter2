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

**Suchbegriffe verwalten:**
/suche <Begriff> - Neuen Suchbegriff erstellen
/liste - Aktive Suchbegriffe anzeigen
/aendern <Alt> <Neu> - Suchbegriff umbenennen
/loeschen <Begriff> - Suchbegriff löschen

**Einstellungen:**
/pausieren <Begriff> - Suchbegriff pausieren
/fortsetzen <Begriff> - Suchbegriff fortsetzen
/frequenz <Begriff> <Zeit> - Suchfrequenz ändern (60s, 5m, 15m)
/stumm <Begriff> [Dauer] - Benachrichtigungen stummschalten
/laut <Begriff> - Stummschaltung aufheben

**Verwaltung:**
/export - Suchbegriffe als Datei exportieren

**Beispiele:**
`/suche "WW2 Medaille"`
`/frequenz "Wehrmacht Helm" 5m`
`/stumm "WW2 Medaille" 30m`

Der Bot durchsucht derzeit: **Militaria321.com**"""

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
    
    # Check if keyword already exists
    existing = await keyword_service.get_user_keyword(user.id, keyword_text)
    if existing:
        await message.answer(f"⚠️ Suchbegriff **'{keyword_text}'** existiert bereits.", parse_mode="Markdown")
        return
    
    # Create new keyword
    try:
        keyword = await keyword_service.create_keyword(user.id, keyword_text)
        
        # Create inline keyboard with management options
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Statistiken", callback_data=f"stats_{keyword.id}"),
                InlineKeyboardButton(text="⏸️ Pausieren", callback_data=f"pause_{keyword.id}")
            ],
            [
                InlineKeyboardButton(text="⚙️ Einstellungen", callback_data=f"settings_{keyword.id}"),
                InlineKeyboardButton(text="🗑️ Löschen", callback_data=f"delete_{keyword.id}")
            ]
        ])
        
        success_text = f"""✅ **Suchbegriff erstellt!**

🔍 Begriff: **{keyword_text}**
📊 Status: Aktiv
⏱️ Frequenz: Alle 60 Sekunden
🌐 Plattform: Militaria321.com

Der Bot beginnt sofort mit der Suche. Sie erhalten Benachrichtigungen bei neuen Treffern."""

        await message.answer(success_text, parse_mode="Markdown", reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error creating keyword: {e}")
        await message.answer("❌ Fehler beim Erstellen des Suchbegriffs. Bitte versuchen Sie es erneut.")


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
    """Handle /loeschen command"""
    user = await ensure_user(message.from_user)
    
    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer("❌ Bitte geben Sie den zu löschenden Suchbegriff an.\n\nBeispiel: `/loeschen Wehrmacht Helm`", parse_mode="Markdown")
        return
    
    keyword_text = args[1].strip()
    keyword = await keyword_service.get_user_keyword(user.id, keyword_text)
    
    if not keyword:
        await message.answer(f"❌ Suchbegriff **'{keyword_text}'** nicht gefunden.", parse_mode="Markdown")
        return
    
    # Confirmation keyboard
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ja, löschen", callback_data=f"confirm_delete_{keyword.id}"),
            InlineKeyboardButton(text="❌ Abbrechen", callback_data="cancel_delete")
        ]
    ])
    
    await message.answer(
        f"⚠️ **Suchbegriff löschen?**\n\n🔍 Begriff: **{keyword_text}**\n\nDiese Aktion kann nicht rückgängig gemacht werden.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@router.message(Command("pausieren"))
async def cmd_pause(message: Message):
    """Handle /pausieren command"""
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
        await message.answer(f"⚠️ Suchbegriff **'{keyword_text}'** ist bereits pausiert.", parse_mode="Markdown")
        return
    
    # Pause keyword
    await keyword_service.update_keyword_status(keyword.id, is_active=False)
    
    await message.answer(f"⏸️ Suchbegriff **'{keyword_text}'** wurde pausiert.\n\nVerwenden Sie `/fortsetzen {keyword_text}` um fortzufahren.", parse_mode="Markdown")


@router.message(Command("fortsetzen"))
async def cmd_resume(message: Message):
    """Handle /fortsetzen command"""
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
        await message.answer(f"⚠️ Suchbegriff **'{keyword_text}'** ist bereits aktiv.", parse_mode="Markdown")
        return
    
    # Resume keyword
    await keyword_service.update_keyword_status(keyword.id, is_active=True)
    
    await message.answer(f"▶️ Suchbegriff **'{keyword_text}'** wurde fortgesetzt.\n\nDie Suche läuft wieder.", parse_mode="Markdown")


async def ensure_user(telegram_user) -> User:
    """Ensure user exists in database"""
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