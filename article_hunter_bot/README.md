# Telegram Article Hunter Bot

A sophisticated Telegram bot that monitors militaria321.com, egun.de und kleinanzeigen.de for new listings matching user-defined keywords, delivering immediate push notifications with deep pagination support to ensure no new items are missed.

## Features

- **Multi-Platform Monitoring**: Watches militaria321.com, egun.de & kleinanzeigen.de with shared newness gating
- **Full-Scan Deep Pagination**: Monitors all pages, solving militaria321.com's end-date sorting issue
- **Legacy Rotation Migration**: Old rotate-mode subscriptions are auto-upgraded to full scans
- **German Commands**: `/search`, `/list`, `/check`, `/delete`, `/hilfe`
- **Smart Monitoring**: Only notifies for truly NEW items (strict timestamp gating)
- **Stable ID Tracking**: Uses canonical numeric IDs to prevent duplicate notifications
- **Health Monitoring**: Comprehensive keyword health status and diagnostics
- **German Timezone**: All timestamps displayed in Europe/Berlin
- **Site-Friendly**: Controlled throttling and concurrency to avoid rate limits
- **Extensible Design**: Easy to add new platforms
- **GDPR Consent Automation**: Kleinanzeigen consent banner handled via Playwright

## Quick Start

📋 **For detailed deployment instructions, see [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md)**

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
   MONGO_URL=mongodb://articlehunter:STRONG_PASSWORD@mongo:27017/?authSource=admin
   DB_NAME=article_hunter

   # Deep Pagination Configuration (Optional)
   POLL_MODE=full                # Full-scan enforced; rotate mode disabled
   PRIMARY_PAGES=1               # Always scan these front pages
   POLL_WINDOW=5                 # Rotating window size
   MAX_PAGES_PER_CYCLE=40        # Hard limit on pages per cycle
   DETAIL_CONCURRENCY=4          # Max concurrent detail page fetches
   GRACE_MINUTES=60              # Grace period for items without timestamp
   POLL_INTERVAL_SECONDS=60      # Polling frequency

   # Kleinanzeigen provider (optional overrides)
   ENABLE_KLEINANZEIGEN=true     # Disable only if platform should be skipped
   KA_BASE_DELAY_SEC=2.8         # Polling delay between requests
   KA_BASELINE_DELAY_SEC=5.0     # Baseline delay between requests
   KA_MAX_RETRIES=3              # Request retries before failing
   KA_BACKOFF_429_MIN=20         # Minutes cooldown after HTTP 429
   KA_BACKOFF_403_HOURS=6        # Hours cooldown after HTTP 403
   KA_COOLDOWN_ON_CAPTCHA_MIN=45 # Minutes cooldown after CAPTCHA detection
   KLEINANZEIGEN_MODE=playwright  # Consent resolver: playwright or http
   KLEINANZEIGEN_HEADLESS=true    # Launch chromium in headless mode
   KLEINANZEIGEN_TIMEOUT_MS=20000 # Consent click timeout in ms
   KLEINANZEIGEN_BLOCK_IF_CONSENT_FAIL=true # Stop crawl if banner persists
   NOTIFY_ADMIN_CHAT_ID=0        # Telegram chat for operator alerts (optional)
   ```

3. **Run with Docker Compose:**
   ```bash
   docker-compose up -d
   ```

### Bare Metal

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. **Start MongoDB & create a dedicated user:**
   ```bash
   mongod --dbpath /path/to/data
   mongosh --eval "db.getSiblingDB('admin').createUser({user: 'articlehunter', pwd: 'STRONG_PASSWORD', roles: [{role: 'readWrite', db: 'article_hunter'}, {role: 'read', db: 'admin'}]})"
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

### Database Backups

Always run `mongodump` with the created credentials so the backup is authenticated:

```bash
mongodump \
  --uri="mongodb://articlehunter:STRONG_PASSWORD@localhost:27017/article_hunter?authSource=admin" \
  --out "$(date +%Y-%m-%d)_article_hunter_backup"
```

Store the dump securely and rotate backups regularly.

## Deep Pagination System

The bot solves militaria321.com's critical end-date sorting issue where new items can appear on deeper pages (6+) and get missed by traditional polling:

### Problem Solved
- **Issue**: Militaria321 sorts by auction end date, causing new items to appear on later pages
- **Previous**: Only checking first 5 pages missed new items deeper in results
- **Solution**: Deep pagination ensures complete coverage

### Polling Modes

**Full Mode (Standard)**: Scans all available pages during every cycle to guarantee immediate detection.
```
Example: 45 total pages
Cycle: Scan pages [1-45]
Result: Kein Artikel wird übersehen.
```

**Legacy Rotate Mode (Deaktiviert)**: Historische rotierende Fensterstrategie. Bestehende Keywords werden automatisch auf den Vollmodus migriert.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_MODE` | `full` | Strategy: `full` (rotate disabled) |
| `PRIMARY_PAGES` | `1` | Always scan these front pages |
| `POLL_WINDOW` | `5` | Pages in rotating window |
| `MAX_PAGES_PER_CYCLE` | `40` | Hard limit per cycle |

### Kleinanzeigen Provider Controls

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_KLEINANZEIGEN` | `true` | Toggle kleinanzeigen.de provider |
| `KA_BASE_DELAY_SEC` | `1.0` | Base delay (~1 req/s) between polling requests |
| `KA_BASELINE_DELAY_SEC` | `1.2` | Delay between baseline warmup requests |
| `KA_MAX_RETRIES` | `3` | Max retries per request |
| `KA_BACKOFF_429_MIN` | `20` | Cooldown minutes after 429 responses |
| `KA_BACKOFF_403_HOURS` | `6` | Cooldown hours after 403 responses |
| `KA_COOLDOWN_ON_CAPTCHA_MIN` | `45` | Initial cooldown minutes after CAPTCHA |
| `KLEINANZEIGEN_MODE` | `playwright` | Consent resolver: `playwright` or `http` |
| `KLEINANZEIGEN_HEADLESS` | `true` | Whether to launch Chromium headless |
| `KLEINANZEIGEN_TIMEOUT_MS` | `20000` | Playwright timeout for consent banner (ms) |
| `KLEINANZEIGEN_BLOCK_IF_CONSENT_FAIL` | `true` | Skip crawling if consent stays blocked |
| `NOTIFY_ADMIN_CHAT_ID` | `0` | Telegram chat ID for admin diagnostics |

### Monitoring

The `/list` command shows enhanced telemetry:
```
📝 Wehrmacht Helm
Status: ✅ Läuft — Letzte Prüfung erfolgreich
Letzte Prüfung: 05.10.2025 16:19 Uhr — Letzter Erfolg: 05.10.2025 16:19 Uhr
Baseline: complete
Plattformen: militaria321.com, egun.de, kleinanzeigen.de
Poll: Modus: full (Alle Seiten) — Seiten: ~45
```

## Commands

### `/search <keyword>`
Create a subscription for `<keyword>`. Runs full baseline crawl across ALL pages on militaria321.com, collects organic egun.de results, seeds seen items (no initial notifications), then starts deep pagination polling.

**Example:**
```
/search Wehrmacht Helm
```

**Response:**
```
Suche eingerichtet: "Wehrmacht Helm"
✅ Baseline abgeschlossen – Ich benachrichtige Sie künftig nur bei neuen Angeboten.
⏱️ Frequenz: Alle 60 Sekunden mit Deep-Pagination
```

### `/list`
Show all active keyword subscriptions with health status and polling telemetry.

**Example:**
```
/list
```

**Response:**
```
Ihre aktiven Überwachungen:

📝 Wehrmacht Helm
Status: ✅ Läuft — Letzte Prüfung erfolgreich  
Letzte Prüfung: 05.10.2025 16:19 Uhr — Letzter Erfolg: 05.10.2025 16:19 Uhr
Baseline: complete
Plattformen: militaria321.com, egun.de, kleinanzeigen.de
Poll: Modus: full (Alle Seiten) — Seiten: ~45

[🔍 Diagnostik] [🗑️ Löschen]
```

### `/check <keyword>`
Manual verification and backfill command. Detects any pending/unprocessed listings that might have been missed (e.g., if bot was offline) and processes them accordingly. Sends notifications for genuinely new items found during backfill.

**Example:**
```
/check Wehrmacht Helm
```

**Response:**
```
🔍 Manuelle Verifikation abgeschlossen: Wehrmacht Helm

📊 Suchergebnisse:
• Plattformen: militaria321.com, egun.de
• Seiten durchsucht: 45 (militaria321.com) + 12 (egun.de)
• Artikel gefunden: 1,124

🔄 Nachbearbeitung (Backfill):
• Unverarbeitete Artikel: 3
• Neue Benachrichtigungen: 2
• Bereits bekannte Artikel: 1

✅ 2 neue Artikel wurden nachträglich benachrichtigt!
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

### `/clear`
Delete all your keywords with two-step confirmation.

### `/hilfe`
Show comprehensive help message with all commands.

## Notification Format

When a truly NEW item is found, you'll receive:

```
🔍 Neues Angebot gefunden

Suchbegriff: Wehrmacht Helm
Titel: Original Wehrmacht M35 Stahlhelm
Preis: 1.234,56 €
Plattform: egun.de
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
| `MONGO_URL` | No | `mongodb://articlehunter:password@localhost:27017/?authSource=admin` | MongoDB connection string (include user/password) |
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
