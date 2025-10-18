# Telegram Article Hunter Bot - Deployment Guide

This guide will help you deploy the Article Hunter Bot on your own server so it can monitor militaria321.com, egun.de und kleinanzeigen.de 24/7 and send you notifications.

## What You Need

1. **A Linux Server** (Ubuntu 20.04+ or Debian 11+ recommended)
   - Can be a VPS like DigitalOcean, Hetzner, AWS EC2, etc.
   - Minimum: 1GB RAM, 1 CPU, 10GB storage
   - Must be always online to monitor continuously

2. **A Telegram Bot Token**
   - Message [@BotFather](https://t.me/botfather) on Telegram
   - Send `/newbot` and choose a name like "My Article Hunter"
   - Save the token (looks like `123456789:ABCdefGHIjklMNOpqrSTUvwxyz`)

## Step-by-Step Installation

### 1. Connect to Your Server

If using a VPS, connect via SSH:
```bash
ssh root@your-server-ip
# or
ssh ubuntu@your-server-ip
```

### 2. Update System

```bash
sudo apt update
sudo apt upgrade -y
```

### 3. Install Required Software

```bash
# Install Python 3.11+
sudo apt install -y python3 python3-pip python3-venv git

# Install MongoDB
wget -qO - https://www.mongodb.org/static/pgp/server-6.0.asc | sudo apt-key add -
echo "deb [ arch=amd64,arm64 ] https://repo.mongodb.org/apt/ubuntu focal/mongodb-org/6.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-6.0.list
sudo apt update
sudo apt install -y mongodb-org

# Start MongoDB
sudo systemctl start mongod
sudo systemctl enable mongod
```

### 4. Download the Bot Code

```bash
# Go to home directory
cd ~

# Clone the repository (replace with your GitHub URL)
git clone https://github.com/yourusername/article-hunter-bot.git
cd article-hunter-bot
```

### 5. Set Up Python Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 6. Configure the Bot

```bash
# Copy example config
cp .env.example .env

# Edit configuration
nano .env
```

**Edit the `.env` file with your settings:**
```bash
# Your bot token from BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxyz

# Database settings (authentication strongly recommended)
MONGO_URL=mongodb://articlehunter:STRONG_PASSWORD@localhost:27017/?authSource=admin
DB_NAME=article_hunter_prod

# Deep Pagination Settings (optional - these are good defaults)
POLL_MODE=full
PRIMARY_PAGES=1
POLL_WINDOW=5
MAX_PAGES_PER_CYCLE=40
DETAIL_CONCURRENCY=4
GRACE_MINUTES=60
POLL_INTERVAL_SECONDS=60

# Your Telegram user ID (optional, for admin commands)
ADMIN_TELEGRAM_IDS=

# Kleinanzeigen consent resolver (`http` or `playwright`)
KLEINANZEIGEN_RESOLVER=http
```

### 6b. Create a dedicated MongoDB user (recommended)

Creating a user with username/password ensures you can run backups securely:

```bash
mongosh
```

Inside the Mongo shell:

```javascript
use admin
db.createUser({
  user: "articlehunter",
  pwd: "STRONG_PASSWORD",
  roles: [
    { role: "readWrite", db: "article_hunter_prod" },
    { role: "read", db: "admin" }
  ]
})
exit
```

Afterwards adjust `MONGO_URL` in `.env` so the credentials match the newly created user.

**To find your Telegram user ID:**
1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It will reply with your user ID (like `123456789`)
3. Add this number to `ADMIN_TELEGRAM_IDS=123456789`

Save the file: Press `Ctrl+O`, then `Enter`, then `Ctrl+X`

### 6c. Gesicherte MongoDB-Backups erstellen

Führen Sie Backups immer mit den erzeugten Zugangsdaten aus, damit der Dump authentifiziert ist:

```bash
mongodump \
  --uri="mongodb://articlehunter:STRONG_PASSWORD@localhost:27017/article_hunter_prod?authSource=admin" \
  --out "$(date +%Y-%m-%d)_article_hunter_backup"
```

Bewahren Sie die Sicherungen außerhalb des Servers auf und rotieren Sie sie regelmäßig.

### 7. Test the Bot

```bash
# Make sure you're in the bot directory
cd ~/article-hunter-bot

# Activate virtual environment
source venv/bin/activate

# Test run
python simple_bot.py
```

You should see:
```
2025-XX-XX XX:XX:XX - Starting Article Hunter Bot...
2025-XX-XX XX:XX:XX - Run polling for bot @your_bot_name
```

If it works, press `Ctrl+C` to stop it.

### 8. Set Up as System Service (24/7 Running)

Create a service file:
```bash
sudo nano /etc/systemd/system/article-hunter-bot.service
```

**Copy this content (replace `ubuntu` with your username and update paths):**
```ini
[Unit]
Description=Telegram Article Hunter Bot
After=network.target mongod.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/article-hunter-bot
Environment=PATH=/home/ubuntu/article-hunter-bot/venv/bin
ExecStart=/home/ubuntu/article-hunter-bot/venv/bin/python simple_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Activate the service:**
```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable article-hunter-bot

# Start the service
sudo systemctl start article-hunter-bot

# Check if it's running
sudo systemctl status article-hunter-bot
```

You should see `Active: active (running)` in green.

### 9. Check Logs

```bash
# View live logs
sudo journalctl -u article-hunter-bot -f

# View recent logs
sudo journalctl -u article-hunter-bot --since="1 hour ago"
```

### 10. Run secure MongoDB backups

Always perform backups with authentication so dumps stay protected:

```bash
mongodump \
  --uri="mongodb://articlehunter:STRONG_PASSWORD@localhost:27017/article_hunter_prod?authSource=admin" \
  --out "$(date +%Y-%m-%d)_article_hunter_backup"
```

You will be prompted for the password if it is not embedded in the URI. Store the dumps in a secure location and rotate them regularly.

## Test Your Bot

1. **Find your bot on Telegram:** Search for the bot name you created
2. **Send `/hilfe`** to see all available commands
3. **Try a search:** `/search Wehrmacht Helm`
4. **Check status:** `/list`

## Bot Commands for Users

Once your bot is running, users can use these commands:

| Command | What it does |
|---------|-------------|
| `/search Suchbegriff` | Start monitoring for "Suchbegriff" |
| `/list` | Show all active monitors with status |
| `/check Suchbegriff` | Manual search without notifications |
| `/delete Suchbegriff` | Stop monitoring "Suchbegriff" |
| `/clear` | Delete all your monitors |
| `/hilfe` | Show detailed help |

## Advanced Configuration

### Docker Deployment (Alternative)

If you prefer Docker:

```bash
# Copy example config first
cp .env.example .env
# Edit .env with your bot token

# Run with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f bot
```

### Performance Tuning

For servers with limited resources:

```bash
# Edit .env
nano .env
```

Add these settings:
```bash
# Reduce concurrent requests
DETAIL_CONCURRENCY=2

# Increase polling interval (check less frequently)
POLL_INTERVAL_SECONDS=120

# Smaller page windows
POLL_WINDOW=3
MAX_PAGES_PER_CYCLE=20
```

### Multiple Users

The bot supports unlimited users automatically. Each user's data is kept separate.

## Troubleshooting

### Bot Won't Start
```bash
# Check logs
sudo journalctl -u article-hunter-bot --since="10 minutes ago"

# Common issues:
# 1. Wrong bot token - check .env file
# 2. MongoDB not running: sudo systemctl start mongod
# 3. Python errors - check if all dependencies installed
```

### No Notifications Received
1. **Check bot status:** Send `/list` to your bot
2. **Verify monitoring is active:** Should show "✅ Läuft"
3. **Check if items are truly new:** Bot only sends notifications for items posted after you started monitoring

### High CPU Usage
```bash
# Check current usage
htop

# If high, edit config to reduce load:
nano .env
# Set: POLL_INTERVAL_SECONDS=180
# Set: DETAIL_CONCURRENCY=2

# Restart bot
sudo systemctl restart article-hunter-bot
```

### Update the Bot

When new versions are available:
```bash
cd ~/article-hunter-bot
git pull
sudo systemctl restart article-hunter-bot
```

## Security Notes

1. **Keep your bot token secret** - never share it publicly
2. **Set up firewall:** Only allow necessary ports (SSH, HTTP if needed)
3. **Regular updates:** Keep your server and bot updated
4. **Monitor logs:** Check for unusual activity

## Support

If you encounter issues:

1. **Check the logs first:** `sudo journalctl -u article-hunter-bot`
2. **Verify configuration:** Make sure `.env` file is correct
3. **Test manually:** Stop service and run `python simple_bot.py` directly
4. **Check dependencies:** `pip list` in activated virtual environment

## What the Bot Does

- **Monitors militaria321.com, egun.de & kleinanzeigen.de** every 60 seconds for new items
- **Deep Pagination**: Checks ALL pages, not just the first few
- **Smart Detection**: Only notifies for genuinely new items
- **German Interface**: All commands and messages in German
- **Stable**: Runs 24/7 with automatic restart on errors

Your bot is now ready to help you find rare militaria items! 🎖️
