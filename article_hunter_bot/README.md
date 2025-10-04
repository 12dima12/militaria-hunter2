# Telegram Article Hunter Bot

A production-ready Telegram bot that monitors militaria321.com for NEW listings matching user keywords and sends immediate push notifications.

## Features

- **German Commands**: `/search`, `/check`, `/delete`
- **Smart Monitoring**: Only notifies for truly NEW items (strict timestamp gating)
- **Fixed Polling**: 60-second intervals for all keywords
- **German Timezone**: All timestamps displayed in Europe/Berlin
- **Extensible Design**: Easy to add new platforms

## Quick Start

### Docker (Recommended)

1. **Clone and setup:**
   ```bash
   git clone <repo-url>
   cd article_hunter_bot
   cp .env.example .env
   ```

2. **Configure environment:**
   Edit `.env` with your settings:
   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   MONGO_URL=mongodb://mongo:27017
   DB_NAME=article_hunter
   ```

3. **Run with Docker Compose:**
   ```bash
   docker-compose up -d
   ```

### Bare Metal

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start MongoDB:**
   ```bash
   mongod --dbpath /path/to/data
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

4. **Run the bot:**
   ```bash
   python main.py
   ```

## Commands

### `/search <keyword>`
Create a subscription for `<keyword>`. Runs full baseline crawl across ALL pages on militaria321.com, seeds seen items (no initial notifications), then starts 60-second polling.

**Example:**
```
/search Wehrmacht Helm
```

**Response:**
```
Suche eingerichtet: "Wehrmacht Helm"
✅ Baseline abgeschlossen – Ich benachrichtige Sie künftig nur bei neuen Angeboten.
⏱️ Frequenz: Alle 60 Sekunden
```

### `/check <keyword>`
Run full re-scan NOW (crawl all pages), update database, show page/item counts. No notifications sent.

**Example:**
```
/check Wehrmacht Helm
```

**Response:**
```
Vollsuche abgeschlossen: "Wehrmacht Helm"
• militaria321.com: 12 Seiten, 287 Produkte
```

### `/delete <keyword>`
Remove the subscription and stop scheduled polling.

**Example:**
```
/delete Wehrmacht Helm
```

**Response:**
```
Überwachung für "Wehrmacht Helm" wurde gelöscht.
```

## Notification Format

When a truly NEW item is found, you'll receive:

```
🔍 Neues Angebot gefunden

Suchbegriff: Wehrmacht Helm
Titel: Original Wehrmacht M35 Stahlhelm
Preis: 1.234,56 €
Plattform: militaria321.com
Bild: 🖼️ Thumbnail
Gefunden: 04.12.2024 15:30 Uhr
Inseriert am: 04.12.2024 14:15 Uhr

[Öffnen] [Keyword löschen]
```

## Technical Details

### Newness Logic
Items are only pushed if ALL conditions are met:
1. **Has posted_ts AND posted_ts >= since_ts** (UTC-aware)
2. **listing_key not in seen_listing_keys**
3. **Notification passes unique-idempotency guard**

If `posted_ts` is missing: allowed within 60-minute grace window after `/search`

### Architecture
- **Database**: MongoDB with proper indexes
- **Polling**: APScheduler with fixed 60-second intervals
- **HTTP**: Realistic headers including `Accept-Encoding: br,gzip,deflate`
- **Timezone**: Germany/Berlin for display, UTC for storage
- **Logging**: Structured JSON logs for decisions and summaries

### Collections
- `users`: User data
- `keywords`: Subscriptions with seen_listing_keys
- `listings`: Cached listing data
- `notifications`: Idempotency tracking

## Extending to New Platforms

To add a new platform:

1. **Create provider:**
   ```python
   # providers/newsite.py
   class NewSiteProvider(BaseProvider):
       @property
       def platform_name(self) -> str:
           return "newsite.com"
       
       async def search(self, keyword, since_ts=None, crawl_all=False):
           # Implement search logic
           pass
   ```

2. **Register provider:**
   ```python
   # providers/__init__.py
   def get_all_providers():
       return {
           "militaria321.com": Militaria321Provider(),
           "newsite.com": NewSiteProvider(),
       }
   ```

3. **Update SearchService** to use all providers

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Bot token from @BotFather |
| `MONGO_URL` | No | `mongodb://localhost:27017` | MongoDB connection string |
| `DB_NAME` | No | `article_hunter` | Database name |
| `ADMIN_TELEGRAM_IDS` | No | - | Comma-separated admin user IDs |

## Troubleshooting

### Bot not responding
1. Check `TELEGRAM_BOT_TOKEN` is correct
2. Verify bot is started with `/start` command
3. Check logs for errors

### No notifications
1. Ensure keyword has active subscription (`/search` was run)
2. Check if items are truly NEW (have recent `posted_ts`)
3. Verify 60-second polling is active

### Database issues
1. Ensure MongoDB is running
2. Check `MONGO_URL` configuration
3. Verify database permissions

## License

MIT License - see LICENSE file for details.
