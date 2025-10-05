#!/usr/bin/env python3
"""
Test the verification block functionality
"""

import asyncio
import sys
import os
from datetime import datetime, timezone

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from services.search_service import SearchService
from providers.militaria321 import Militaria321Provider
from models import User, Keyword, Listing
from zoneinfo import ZoneInfo

# Mock the simple_bot function for testing
async def _format_verification_block(last_item_meta: dict, keyword_text: str, search_service) -> str:
    """Format verification block for last found listing"""
    listing = last_item_meta["listing"]
    page_index = last_item_meta["page_index"]
    
    # If posted_ts is missing, fetch it for display only
    had_to_fetch_detail = False
    if listing.posted_ts is None:
        try:
            provider = search_service.providers.get("militaria321.com")
            if provider:
                await provider.fetch_posted_ts_batch([listing], concurrency=1)
                had_to_fetch_detail = True
        except Exception as e:
            print(f"Failed to fetch posted_ts for verification: {e}")
    
    # Log verification event
    print({
        "event": "verification_last_item",
        "platform": listing.platform,
        "page_index": page_index,
        "listing_key": f"{listing.platform}:{listing.platform_id}",
        "posted_ts_utc": listing.posted_ts.isoformat() if listing.posted_ts else None,
        "had_to_fetch_detail": had_to_fetch_detail
    })
    
    # Format timestamps
    berlin_tz = ZoneInfo("Europe/Berlin")
    now_berlin = datetime.now(berlin_tz).strftime("%d.%m.%Y %H:%M Uhr")
    
    if listing.posted_ts:
        posted_berlin = listing.posted_ts.replace(tzinfo=timezone.utc).astimezone(berlin_tz).strftime("%d.%m.%Y %H:%M Uhr")
    else:
        posted_berlin = "/"
    
    # Format price
    if listing.price_value:
        provider = Militaria321Provider()
        price_formatted = provider.format_price_de(listing.price_value, listing.price_currency)
    else:
        price_formatted = "/"
    
    # Build verification block with exact format
    verification_text = (
        f"🎖️ Der letzte gefundene Artikel auf Seite {page_index}\n\n"
        f"🔍 Suchbegriff: {keyword_text}\n"
        f"📝 Titel: {listing.title}\n"
        f"💰 {price_formatted}\n\n"
        f"🌐 Plattform: militaria321.com\n"
        f"🕐 Gefunden: {now_berlin}\n"
        f"✏️ Eingestellt am: {posted_berlin}"
    )
    
    return verification_text

async def test_verification_block():
    """Test verification block formatting"""
    
    print("=== Testing Verification Block ===\n")
    
    # Initialize services
    db_manager = DatabaseManager()
    await db_manager.initialize()
    search_service = SearchService(db_manager)
    
    # Create mock last item metadata
    mock_listing = Listing(
        platform="militaria321.com",
        platform_id="123456",
        title="Wehrmacht Stahlhelm M40 - Originalzustand",
        url="https://www.militaria321.com/auktion/123456",
        price_value=249.50,
        price_currency="EUR",
        posted_ts=datetime.now(timezone.utc) - timedelta(hours=2)
    )
    
    last_item_meta = {
        "page_index": 5,
        "listing": mock_listing
    }
    
    # Test verification block formatting
    verification_text = await _format_verification_block(
        last_item_meta, "helm", search_service
    )
    
    print("Generated Verification Block:")
    print("=" * 50)
    print(verification_text)
    print("=" * 50)
    
    # Test with missing posted_ts
    mock_listing_no_ts = Listing(
        platform="militaria321.com",
        platform_id="789012",
        title="Uniform Jacke Original",
        url="https://www.militaria321.com/auktion/789012",
        price_value=None,
        price_currency=None,
        posted_ts=None
    )
    
    last_item_meta_no_ts = {
        "page_index": 3,
        "listing": mock_listing_no_ts
    }
    
    print("\n\nVerification Block (no posted_ts, no price):")
    print("=" * 50)
    verification_text_no_ts = await _format_verification_block(
        last_item_meta_no_ts, "uniform", search_service
    )
    print(verification_text_no_ts)
    print("=" * 50)
    
    await db_manager.close()
    print("\n✓ Verification block test completed!")

if __name__ == "__main__":
    from datetime import timedelta
    asyncio.run(test_verification_block())