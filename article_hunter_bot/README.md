# Telegram Article Hunter Bot

A sophisticated Telegram bot that monitors militaria321.com for new listings matching user-defined keywords, delivering immediate push notifications with deep pagination support to ensure no new items are missed.

## Features

- **Deep Pagination System**: Monitors all pages, not just the first few, solving militaria321.com's end-date sorting issue
- **Intelligent Polling**: Two modes (full-scan and rotating deep-scan) for efficient coverage
- **German Commands**: `/search`, `/list`, `/check`, `/delete`, `/hilfe`
- **Smart Monitoring**: Only notifies for truly NEW items (strict timestamp gating)
- **Stable ID Tracking**: Uses canonical numeric IDs to prevent duplicate notifications
- **Health Monitoring**: Comprehensive keyword health status and diagnostics
- **German Timezone**: All timestamps displayed in Europe/Berlin
- **Site-Friendly**: Controlled throttling and concurrency to avoid rate limits
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
   
   # Deep Pagination Configuration (Optional)
   POLL_MODE=rotate              # "full" or "rotate"
   PRIMARY_PAGES=1               # Always scan these front pages
   POLL_WINDOW=5                 # Rotating window size
   MAX_PAGES_PER_CYCLE=40        # Hard limit on pages per cycle
   DETAIL_CONCURRENCY=4          # Max concurrent detail page fetches
   GRACE_MINUTES=60              # Grace period for items without timestamp
   POLL_INTERVAL_SECONDS=60      # Polling frequency
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
   python simple_bot.py
   ```

## Deep Pagination System

The bot solves militaria321.com's critical end-date sorting issue where new items can appear on deeper pages (6+) and get missed by traditional polling:

### Problem Solved
- **Issue**: Militaria321 sorts by auction end date, causing new items to appear on later pages
- **Previous**: Only checking first 5 pages missed new items deeper in results
- **Solution**: Deep pagination ensures complete coverage

### Polling Modes

**Rotate Mode (Default)**: Efficient rotating window strategy
```
Example: 50 total pages, window=5, cursor=8
Cycle 1: Scan pages [1, 8, 9, 10, 11, 12]
Cycle 2: Scan pages [1, 13, 14, 15, 16, 17]  
Cycle 3: Scan pages [1, 18, 19, 20, 21, 22]
Result: Complete coverage over time, site-friendly
```

**Full Mode**: Scan all pages every cycle
```
Best for: Keywords with <40 pages
Trade-off: Immediate detection but higher resource usage
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_MODE` | `rotate` | Strategy: `full` or `rotate` |
| `PRIMARY_PAGES` | `1` | Always scan these front pages |
| `POLL_WINDOW` | `5` | Pages in rotating window |
| `MAX_PAGES_PER_CYCLE` | `40` | Hard limit per cycle |

### Monitoring

The `/list` command shows enhanced telemetry:
```
📝 Wehrmacht Helm
Status: ✅ Läuft — Letzte Prüfung erfolgreich
Letzte Prüfung: 05.10.2025 16:19 Uhr — Letzter Erfolg: 05.10.2025 16:19 Uhr
Baseline: complete
Plattformen: militaria321.com
Poll: Modus: rotate — Seiten: ~45 — Fenster: 12-16
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
