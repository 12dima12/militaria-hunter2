#!/usr/bin/env python3
"""
Test real search functionality with logging
"""

import asyncio
import sys
import os
import logging
from datetime import datetime

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from providers.militaria321 import Militaria321Provider

# Configure logging to see the structured logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test_real_search():
    """Test real search with popular keywords"""
    
    print("=== Testing Real Search with Logging ===\n")
    
    provider = Militaria321Provider()
    
    # Test with different keywords that might have results
    keywords = ["wh", "orden", "badge", "button"]
    
    for keyword in keywords:
        print(f"\nTesting keyword: '{keyword}'")
        print("-" * 40)
        
        try:
            # Test baseline crawl (first page only for speed)
            result = await provider.search(
                keyword=keyword,
                crawl_all=False  # Just first page for testing
            )
            
            print(f"✓ Found {len(result.items)} items")
            print(f"✓ Pages scanned: {result.pages_scanned}")
            print(f"✓ Total count: {result.total_count}")
            
            if result.items:
                # Show first few items
                for i, item in enumerate(result.items[:3]):
                    print(f"  Item {i+1}: {item.title[:60]}...")
                    print(f"    ID: {item.platform_id}")
                    print(f"    URL: {item.url}")
                    print(f"    Price: {item.price_value} {item.price_currency or ''}")
                    print(f"    Posted: {item.posted_ts}")
                    
                # Test posted_ts enrichment on first item
                first_item = result.items[0]
                if first_item.posted_ts is None:
                    print(f"\n  Testing posted_ts enrichment for: {first_item.platform_id}")
                    await provider.fetch_posted_ts_batch([first_item], concurrency=1)
                    print(f"  After enrichment: {first_item.posted_ts}")
                
                break  # Found results, no need to test more keywords
                
        except Exception as e:
            print(f"✗ Error testing '{keyword}': {e}")
            continue
    
    print(f"\n✓ Real search test completed!")

if __name__ == "__main__":
    asyncio.run(test_real_search())