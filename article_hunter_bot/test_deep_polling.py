#!/usr/bin/env python3
"""
Test script for deep pagination functionality
"""
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv
import os
import sys
import unicodedata

# Add current directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from models import Keyword
from services.search_service import SearchService

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_deep_polling():
    """Test the deep polling functionality"""
    
    # Initialize database
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    # Initialize search service
    search_service = SearchService(db_manager)
    
    print("🚀 Testing Deep Pagination System...")
    print("=" * 50)
    
    # Test with a keyword that has many results
    test_keyword = "medaille"
    
    # Get or create a test keyword
    normalized = unicodedata.normalize('NFKC', test_keyword.lower())
    
    # Look for existing keyword
    existing_keywords = await db_manager.get_user_keywords("test_user_id", active_only=False)
    test_keyword_obj = None
    
    for kw in existing_keywords:
        if kw.normalized_keyword == normalized:
            test_keyword_obj = kw
            break
    
    if not test_keyword_obj:
        # Create test keyword
        test_keyword_obj = Keyword(
            user_id="test_user_id",
            original_keyword=test_keyword,
            normalized_keyword=normalized,
            since_ts=datetime.utcnow(),
            poll_mode="rotate",
            poll_window=5,
            poll_cursor_page=1
        )
        test_keyword_obj = await db_manager.create_keyword(test_keyword_obj)
        print(f"✅ Created test keyword: {test_keyword}")
    else:
        print(f"✅ Using existing keyword: {test_keyword}")
    
    print(f"📊 Keyword ID: {test_keyword_obj.id}")
    print(f"📊 Poll Mode: {test_keyword_obj.poll_mode}")
    print(f"📊 Cursor Page: {test_keyword_obj.poll_cursor_page}")
    print(f"📊 Poll Window: {test_keyword_obj.poll_window}")
    print(f"📊 Seen Keys Count: {len(test_keyword_obj.seen_listing_keys)}")
    print()
    
    # Test 1: Deep polling search
    print("🔍 Test 1: Running deep polling search...")
    try:
        results = await search_service.search_keyword(test_keyword_obj, dry_run=True)
        
        print(f"✅ Deep polling completed!")
        print(f"📈 New items found: {len(results)}")
        
        if results:
            print("📝 Sample results:")
            for i, item in enumerate(results[:3]):
                print(f"   {i+1}. {item.title[:60]}... - {item.price_value}€")
        
        print()
        
    except Exception as e:
        print(f"❌ Deep polling failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Test 2: Check page determination logic
    print("🔄 Test 2: Testing page determination logic...")
    
    pages_to_scan = search_service._determine_pages_to_scan(
        poll_mode="rotate",
        cursor_page=10,
        window_size=5,
        total_pages_estimate=50,
        primary_pages=1,
        max_pages_per_cycle=40
    )
    
    print(f"✅ Rotate mode - Pages to scan: {pages_to_scan}")
    
    pages_to_scan_full = search_service._determine_pages_to_scan(
        poll_mode="full",
        cursor_page=1,
        window_size=5,
        total_pages_estimate=50,
        primary_pages=1,
        max_pages_per_cycle=40
    )
    
    print(f"✅ Full mode - Pages to scan: {pages_to_scan_full[:10]}{'...' if len(pages_to_scan_full) > 10 else ''}")
    print()
    
    # Test 3: Provider single page mode
    print("🔧 Test 3: Testing provider single page mode...")
    try:
        from providers.militaria321 import Militaria321Provider
        provider = Militaria321Provider()
        
        # Test single page fetch
        result = await provider.search(
            keyword=test_keyword,
            mode="poll",
            poll_pages=1,
            page_start=5
        )
        
        print(f"✅ Single page fetch completed!")
        print(f"📊 Items found on page 5: {len(result.items)}")
        print(f"📊 Pages scanned: {result.pages_scanned}")
        print(f"📊 Last page index: {result.last_page_index}")
        
        if result.items:
            print(f"📝 Sample item: {result.items[0].title[:60]}...")
        
    except Exception as e:
        print(f"❌ Single page test failed: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("🎉 Deep Pagination Testing Complete!")
    
    # Cleanup
    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(test_deep_polling())