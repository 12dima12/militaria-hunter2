#!/usr/bin/env python3
"""
Test the egun.de provider
"""
import sys
import asyncio
import logging
sys.path.insert(0, '/app/backend')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from providers.egun import EgunProvider

async def test_provider():
    """Test the provider with different keywords"""
    provider = EgunProvider()
    
    test_keywords = [
        "Büchse",
        "Pistole",
        "Messer",
        "nonexistentterm12345",  # Should return 0
    ]
    
    for keyword in test_keywords:
        print("\n" + "=" * 80)
        print(f"Testing keyword: {keyword}")
        print("=" * 80)
        
        result = await provider.search(keyword, sample_mode=True)
        
        print(f"\nResults:")
        print(f"  Total count: {result.total_count}")
        print(f"  Items found: {len(result.items)}")
        print(f"  Has more: {result.has_more}")
        
        if result.items:
            print(f"\nFirst 3 items:")
            for i, item in enumerate(result.items[:3], 1):
                # Format price
                if item.price_value:
                    from decimal import Decimal
                    price_formatted = provider.format_price_de(
                        Decimal(str(item.price_value)), 
                        item.price_currency or "EUR"
                    )
                else:
                    price_formatted = "N/A"
                
                print(f"\n  {i}. {item.title}")
                print(f"     Price: {price_formatted}")
                print(f"     URL: {item.url}")
                print(f"     ID: {item.platform_id}")
                
                # Verify the match
                matches = provider.matches_keyword(item.title, keyword)
                print(f"     Matches keyword: {matches}")
        else:
            print("\n  No items found")

if __name__ == "__main__":
    asyncio.run(test_provider())
