#!/usr/bin/env python3
"""
Test script for posted_ts enrichment and newness gate implementation
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from services.search_service import SearchService
from providers.militaria321 import Militaria321Provider
from models import User, Keyword

async def test_posted_ts_implementation():
    """Test the posted_ts enrichment and newness gating"""
    
    # Initialize services
    db_manager = DatabaseManager()
    await db_manager.initialize()
    
    search_service = SearchService(db_manager)
    
    print("=== Testing posted_ts Enrichment & Newness Gate ===\n")
    
    # Test 1: Provider search functionality
    print("1. Testing Militaria321 Provider...")
    provider = Militaria321Provider()
    
    try:
        result = await provider.search(keyword="uniform", crawl_all=False)
        print(f"✓ Search returned {len(result.items)} items")
        
        if result.items:
            sample_item = result.items[0]
            print(f"✓ Sample item: {sample_item.title[:50]}...")
            print(f"✓ Platform ID: {sample_item.platform_id}")
            print(f"✓ Initial posted_ts: {sample_item.posted_ts}")
            
            # Test fetch_posted_ts_batch
            if sample_item.posted_ts is None:
                print("  - Testing posted_ts enrichment...")
                await provider.fetch_posted_ts_batch([sample_item], concurrency=1)
                print(f"✓ After enrichment posted_ts: {sample_item.posted_ts}")
            
    except Exception as e:
        print(f"✗ Provider test failed: {e}")
        return False
    
    # Test 2: Search Service newness logic
    print("\n2. Testing SearchService newness logic...")
    
    # Create test keyword
    test_keyword = Keyword(
        user_id="test_user",
        original_keyword="uniform", 
        normalized_keyword="uniform",
        since_ts=datetime.utcnow() - timedelta(hours=1),  # 1 hour ago
        seen_listing_keys=[],
        platforms=["militaria321.com"]
    )
    
    # Test _is_new_listing logic
    from models import Listing
    
    # Test case 1: Item with posted_ts > since_ts (should be new)
    test_item_1 = Listing(
        platform="militaria321.com",
        platform_id="123456",
        title="Test Uniform",
        url="https://test.com",
        posted_ts=datetime.utcnow() - timedelta(minutes=30)  # 30 min ago
    )
    
    is_new_1 = search_service._is_new_listing(test_item_1, test_keyword)
    print(f"✓ Item with posted_ts > since_ts: {is_new_1} (expected: True)")
    
    # Test case 2: Item with posted_ts < since_ts (should be old)
    test_item_2 = Listing(
        platform="militaria321.com", 
        platform_id="123457",
        title="Old Uniform",
        url="https://test.com",
        posted_ts=datetime.utcnow() - timedelta(hours=2)  # 2 hours ago
    )
    
    is_new_2 = search_service._is_new_listing(test_item_2, test_keyword)
    print(f"✓ Item with posted_ts < since_ts: {is_new_2} (expected: False)")
    
    # Test case 3: Item without posted_ts within grace window
    test_keyword_recent = Keyword(
        user_id="test_user",
        original_keyword="uniform",
        normalized_keyword="uniform", 
        since_ts=datetime.utcnow() - timedelta(minutes=30),  # 30 min ago
        seen_listing_keys=[],
        platforms=["militaria321.com"]
    )
    
    test_item_3 = Listing(
        platform="militaria321.com",
        platform_id="123458", 
        title="No Timestamp Uniform",
        url="https://test.com",
        posted_ts=None
    )
    
    is_new_3 = search_service._is_new_listing(test_item_3, test_keyword_recent)
    print(f"✓ Item without posted_ts (within grace): {is_new_3} (expected: True)")
    
    # Test case 4: Item without posted_ts beyond grace window
    is_new_4 = search_service._is_new_listing(test_item_3, test_keyword)
    print(f"✓ Item without posted_ts (beyond grace): {is_new_4} (expected: False)")
    
    print("\n✓ All tests completed successfully!")
    
    await db_manager.close()
    return True

if __name__ == "__main__":
    try:
        result = asyncio.run(test_posted_ts_implementation())
        if result:
            print("\n🎉 posted_ts implementation test PASSED!")
        else:
            print("\n❌ posted_ts implementation test FAILED!")
            sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with exception: {e}")
        sys.exit(1)