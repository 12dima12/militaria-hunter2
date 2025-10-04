#!/usr/bin/env python3
"""
Test the complete militaria321 provider from article_hunter_bot
"""
import sys
import asyncio
sys.path.insert(0, '/app/article_hunter_bot')

async def test_complete_provider():
    """Test the complete provider implementation"""
    
    print("=" * 80)
    print("TESTING COMPLETE MILITARIA321 PROVIDER")
    print("=" * 80)
    
    try:
        from providers.militaria321 import Militaria321Provider
        
        provider = Militaria321Provider()
        
        print(f"Provider platform: {provider.platform_name}")
        print(f"Base URL: {provider.BASE_URL}")
        print(f"Search URL: {provider.SEARCH_URL}")
        
        print("\n" + "=" * 40)
        print("AVAILABLE METHODS:")
        print("=" * 40)
        
        methods = [method for method in dir(provider) if not method.startswith('__')]
        for method in sorted(methods):
            attr = getattr(provider, method)
            if callable(attr):
                print(f"✓ {method}()")
            else:
                print(f"  {method} = {attr}")
        
        print("\n" + "=" * 40)
        print("TESTING SEARCH FUNCTIONALITY:")
        print("=" * 40)
        
        # Test searches
        test_keywords = ["Brieföffner", "Helm"]
        
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
                    print(f"     Posted: {item.posted_ts}")
                    
            except Exception as e:
                print(f"✗ Search failed: {e}")
                import traceback
                traceback.print_exc()
        
        print("\n" + "=" * 40)
        print("TESTING POSTED_TS FUNCTIONALITY:")
        print("=" * 40)
        
        # Test posted_ts parsing
        try:
            from models import Listing
            
            # Create test listings
            test_listings = [
                Listing(
                    platform="militaria321.com",
                    platform_id="12345",
                    title="Test Item 1",
                    url="https://www.militaria321.com/auktion/12345",
                    posted_ts=None
                )
            ]
            
            print("Testing fetch_posted_ts_batch...")
            await provider.fetch_posted_ts_batch(test_listings, concurrency=1)
            print(f"✓ fetch_posted_ts_batch completed")
            
        except Exception as e:
            print(f"✗ posted_ts testing failed: {e}")
            import traceback
            traceback.print_exc()
        
        print("\n" + "=" * 40)
        print("TESTING KEYWORD MATCHING:")
        print("=" * 40)
        
        # Test keyword matching
        test_cases = [
            ("Wehrmacht Brieföffner", "brieföffner", True),
            ("Endet um 07:39 Uhr", "uhr", False),
            ("Turm mit Uhr", "uhr", True),
        ]
        
        for title, keyword, expected in test_cases:
            try:
                result = provider._matches_keyword(title, keyword)
                status = "✓" if result == expected else "✗"
                print(f"{status} '{title}' matches '{keyword}': {result} (expected {expected})")
            except Exception as e:
                print(f"✗ Error testing '{title}' with '{keyword}': {e}")
        
    except ImportError as e:
        print(f"✗ Failed to import complete provider: {e}")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_complete_provider())