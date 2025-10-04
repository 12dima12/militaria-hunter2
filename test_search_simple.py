#!/usr/bin/env python3
"""
Simple test of militaria321 search functionality
"""
import sys
import asyncio
sys.path.insert(0, '/app/backend')

from providers.militaria321 import Militaria321Provider

async def test_search():
    """Test the search functionality"""
    
    print("=" * 80)
    print("TESTING MILITARIA321 SEARCH")
    print("=" * 80)
    
    provider = Militaria321Provider()
    
    # Test searches
    test_keywords = ["Brieföffner", "Helm", "nonexistentterm12345"]
    
    for keyword in test_keywords:
        print(f"\n--- Testing search for '{keyword}' ---")
        try:
            result = await provider.search(keyword, sample_mode=True)
            print(f"✓ Search completed")
            print(f"  Items found: {len(result.items)}")
            print(f"  Total count: {result.total_count}")
            print(f"  Has more: {result.has_more}")
            print(f"  Pages scanned: {getattr(result, 'pages_scanned', 'N/A')}")
            
            # Show first few results
            for i, item in enumerate(result.items[:3]):
                print(f"  {i+1}. {item.title[:60]}...")
                print(f"     URL: {item.url}")
                print(f"     Price: {item.price_value} {item.price_currency}")
                
        except Exception as e:
            print(f"✗ Search failed: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_search())