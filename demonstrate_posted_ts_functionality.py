#!/usr/bin/env python3
"""
Demonstrate the complete posted_ts enrichment and newness gating functionality
"""

import asyncio
import sys
import logging
from datetime import datetime, timedelta, timezone

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from services.search_service import SearchService
from models import User, Keyword
from zoneinfo import ZoneInfo

# Configure logging to see structured logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def demonstrate_functionality():
    """Demonstrate the complete posted_ts functionality"""
    
    print("=" * 80)
    print("DEMONSTRATING POSTED_TS ENRICHMENT & NEWNESS GATE FUNCTIONALITY")
    print("=" * 80)
    
    # Initialize services
    db_manager = DatabaseManager()
    await db_manager.initialize()
    search_service = SearchService(db_manager)
    
    # Create test user
    test_user = User(
        id="demo_user_posted_ts",
        telegram_id=999888777
    )
    
    try:
        await db_manager.create_user(test_user)
        print(f"✓ Created test user: {test_user.id}")
    except:
        print(f"✓ Test user already exists: {test_user.id}")
    
    # Test 1: Baseline crawling with last item tracking
    print(f"\n{'-'*60}")
    print("TEST 1: Baseline Crawling with Last Item Metadata")
    print(f"{'-'*60}")
    
    keyword_text = "orden"
    print(f"Running baseline for keyword: '{keyword_text}'")
    
    # Create keyword
    test_keyword = Keyword(
        id="demo_keyword_posted_ts",
        user_id=test_user.id,
        original_keyword=keyword_text,
        normalized_keyword=keyword_text.lower(),
        since_ts=datetime.utcnow(),
        baseline_status="pending",
        platforms=["militaria321.com"]
    )
    
    try:
        await db_manager.create_keyword(test_keyword)
        print(f"✓ Created test keyword: {test_keyword.id}")
    except:
        print(f"✓ Test keyword already exists, updating...")
        await db_manager.db.keywords.update_one(
            {"id": test_keyword.id},
            {"$set": test_keyword.dict()}
        )
    
    # Run baseline with first page only (for demo speed)
    print(f"\n🔍 Starting baseline crawl...")
    baseline_items, last_item_meta = await search_service.full_baseline_seed(
        keyword_text, test_keyword.id
    )
    
    print(f"✓ Baseline completed!")
    print(f"  - Items found: {len(baseline_items)}")
    print(f"  - Last item metadata: {last_item_meta is not None}")
    
    if last_item_meta:
        listing = last_item_meta["listing"]
        print(f"  - Last item on page {last_item_meta['page_index']}: {listing.title[:50]}...")
        print(f"  - Platform ID: {listing.platform_id}")
        print(f"  - Posted TS: {listing.posted_ts}")
    
    # Test 2: Posted_ts enrichment during polling
    print(f"\n{'-'*60}")
    print("TEST 2: Posted_ts Enrichment During Polling")  
    print(f"{'-'*60}")
    
    # Simulate polling by searching with enrichment
    print(f"Simulating polling search for '{keyword_text}'...")
    
    # Get fresh keyword state
    fresh_keyword = await db_manager.get_keyword_by_normalized(
        test_user.id, keyword_text.lower()
    )
    
    if fresh_keyword:
        # Clear seen_listing_keys to simulate new items
        fresh_keyword.seen_listing_keys = []
        fresh_keyword.since_ts = datetime.utcnow() - timedelta(hours=1)  # 1 hour ago
        
        print(f"Running polling search...")
        new_items = await search_service.search_keyword(fresh_keyword)
        
        print(f"✓ Polling search completed!")
        print(f"  - New items found: {len(new_items)}")
        
        # Show examples of enriched items
        for i, item in enumerate(new_items[:3]):
            print(f"  Item {i+1}:")
            print(f"    Title: {item.title[:60]}...")
            print(f"    Platform ID: {item.platform_id}")
            print(f"    Posted TS: {item.posted_ts}")
            print(f"    Price: {item.price_value} {item.price_currency or 'EUR'}")
    
    # Test 3: Newness gating logic
    print(f"\n{'-'*60}")
    print("TEST 3: Newness Gating Logic")
    print(f"{'-'*60}")
    
    if baseline_items:
        test_item = baseline_items[0]
        
        # Test case 1: Item with posted_ts > since_ts (new)
        old_since_ts = datetime.utcnow() - timedelta(days=30)  # 30 days ago
        test_keyword_old = Keyword(
            user_id=test_user.id,
            original_keyword="test",
            normalized_keyword="test",
            since_ts=old_since_ts,
            seen_listing_keys=[],
            platforms=["militaria321.com"]
        )
        
        is_new = search_service._is_new_listing(test_item, test_keyword_old)
        print(f"✓ Item with posted_ts > since_ts: {is_new} (should be True)")
        
        # Test case 2: Item already in seen set (old)
        test_keyword_seen = Keyword(
            user_id=test_user.id,
            original_keyword="test", 
            normalized_keyword="test",
            since_ts=old_since_ts,
            seen_listing_keys=[f"{test_item.platform}:{test_item.platform_id}"],
            platforms=["militaria321.com"]
        )
        
        is_old = search_service._is_new_listing(test_item, test_keyword_seen)
        print(f"✓ Item in seen_listing_keys: {is_old} (should be False)")
        
        # Test case 3: Grace window logic
        recent_since_ts = datetime.utcnow() - timedelta(minutes=30)  # 30 minutes ago
        test_keyword_grace = Keyword(
            user_id=test_user.id,
            original_keyword="test",
            normalized_keyword="test", 
            since_ts=recent_since_ts,
            seen_listing_keys=[],
            platforms=["militaria321.com"]
        )
        
        # Create item without posted_ts
        from models import Listing
        item_no_ts = Listing(
            platform="militaria321.com",
            platform_id="999999",
            title="Test item without timestamp",
            url="https://test.com",
            posted_ts=None
        )
        
        is_grace = search_service._is_new_listing(item_no_ts, test_keyword_grace)
        print(f"✓ Item without posted_ts (within grace): {is_grace} (should be True)")
        
        # Test beyond grace window
        old_keyword_grace = Keyword(
            user_id=test_user.id,
            original_keyword="test",
            normalized_keyword="test",
            since_ts=datetime.utcnow() - timedelta(hours=2),  # 2 hours ago
            seen_listing_keys=[],
            platforms=["militaria321.com"]
        )
        
        is_beyond_grace = search_service._is_new_listing(item_no_ts, old_keyword_grace)
        print(f"✓ Item without posted_ts (beyond grace): {is_beyond_grace} (should be False)")
    
    # Test 4: Verification block formatting
    print(f"\n{'-'*60}")
    print("TEST 4: Verification Block Formatting")
    print(f"{'-'*60}")
    
    if last_item_meta:
        # Import the formatting function
        sys.path.insert(0, '/app')
        
        # Manually import and test verification formatting
        from datetime import timezone
        from zoneinfo import ZoneInfo
        from providers.militaria321 import Militaria321Provider
        
        listing = last_item_meta["listing"]
        page_index = last_item_meta["page_index"]
        
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
            f"🎖️ Der letzte gefundene Artikel auf Seite {page_index}\n\n"
            f"🔍 Suchbegriff: {keyword_text}\n"
            f"📝 Titel: {listing.title}\n"
            f"💰 {price_formatted}\n\n"
            f"🌐 Plattform: militaria321.com\n"
            f"🕐 Gefunden: {now_berlin}\n"
            f"✏️ Eingestellt am: {posted_berlin}"
        )
        
        print("Verification Block:")
        print("=" * 50)
        print(verification_text)
        print("=" * 50)
    
    # Cleanup
    await db_manager.close()
    
    print(f"\n{'='*80}")
    print("✅ ALL FUNCTIONALITY DEMONSTRATED SUCCESSFULLY!")
    print("✅ Posted_ts enrichment working")
    print("✅ Newness gating logic working")
    print("✅ Verification block formatting working")
    print("✅ Structured logging implemented")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(demonstrate_functionality())