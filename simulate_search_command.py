#!/usr/bin/env python3
"""
Simulate the complete /search command flow
"""

import asyncio
import sys
import logging
from datetime import datetime, timezone

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from services.search_service import SearchService
from models import User, Keyword
from zoneinfo import ZoneInfo
from providers.militaria321 import Militaria321Provider

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def simulate_search_command():
    """Simulate the complete /search command"""
    
    print("=" * 80)
    print("SIMULATING /search uniform COMMAND")
    print("=" * 80)
    
    # Initialize services
    db_manager = DatabaseManager()
    await db_manager.initialize()
    search_service = SearchService(db_manager)
    
    keyword_text = "uniform"
    user_id = "sim_user"
    
    print(f"🔍 User runs: /search {keyword_text}")
    print(f"📊 Starting baseline crawl...")
    
    # Step 1: Create keyword
    keyword = Keyword(
        id="sim_keyword_uniform", 
        user_id=user_id,
        original_keyword=keyword_text,
        normalized_keyword=keyword_text.lower(),
        since_ts=datetime.utcnow(),
        baseline_status="pending",
        platforms=["militaria321.com"]
    )
    
    # Step 2: Run baseline
    baseline_items, last_item_meta = await search_service.full_baseline_seed(
        keyword_text, keyword.id
    )
    
    # Step 3: Seed seen_listing_keys
    seen_keys = []
    for item in baseline_items:
        listing_key = f"{item.platform}:{item.platform_id}"
        seen_keys.append(listing_key)
    
    print(f"✅ Baseline abgeschlossen – Ich benachrichtige Sie künftig nur bei neuen Angeboten.")
    print(f"⏱️ Frequenz: Alle 60 Sekunden")
    print(f"📊 {len(seen_keys)} Angebote als Baseline erfasst")
    
    # Step 4: Show verification block
    if last_item_meta and last_item_meta.get("listing"):
        listing = last_item_meta["listing"]
        page_index = last_item_meta["page_index"]
        
        # If posted_ts is missing, fetch it for display only
        if listing.posted_ts is None:
            try:
                provider = search_service.providers.get("militaria321.com")
                if provider:
                    print(f"🔄 Fetching posted_ts for verification...")
                    await provider.fetch_posted_ts_batch([listing], concurrency=1)
            except Exception as e:
                print(f"⚠️ Could not fetch posted_ts: {e}")
        
        # Format verification block
        berlin_tz = ZoneInfo("Europe/Berlin")
        now_berlin = datetime.now(berlin_tz).strftime("%d.%m.%Y %H:%M Uhr")
        
        if listing.posted_ts:
            posted_berlin = listing.posted_ts.replace(tzinfo=timezone.utc).astimezone(berlin_tz).strftime("%d.%m.%Y %H:%M Uhr")
        else:
            posted_berlin = "/"
        
        if listing.price_value:
            provider = Militaria321Provider()
            price_formatted = provider.format_price_de(listing.price_value, listing.price_currency)
        else:
            price_formatted = "/"
        
        verification_text = (
            f"\n🎖️ Der letzte gefundene Artikel auf Seite {page_index}\n\n"
            f"🔍 Suchbegriff: {keyword_text}\n"
            f"📝 Titel: {listing.title}\n"
            f"💰 {price_formatted}\n\n"
            f"🌐 Plattform: militaria321.com\n"
            f"🕐 Gefunden: {now_berlin}\n"
            f"✏️ Eingestellt am: {posted_berlin}"
        )
        
        print(verification_text)
    
    print(f"\n" + "=" * 80)
    print("🎉 /search COMMAND SIMULATION COMPLETE!")
    print(f"📈 Found {len(baseline_items)} items across multiple pages")
    print(f"🔄 Polling will now check every 60 seconds for new items")
    print(f"📥 Only items with posted_ts >= {keyword.since_ts.strftime('%Y-%m-%d %H:%M:%S')} UTC will trigger notifications")
    print(f"⏰ Grace window: 60 minutes for items without posted_ts")
    print("=" * 80)
    
    await db_manager.close()

if __name__ == "__main__":
    asyncio.run(simulate_search_command())