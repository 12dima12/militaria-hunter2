#!/usr/bin/env python3
"""
Test search for 'Orden' on both providers
"""
import sys
import asyncio
sys.path.insert(0, '/app/backend')

from providers.militaria321 import Militaria321Provider
from providers.egun import EgunProvider

async def test_orden():
    print("=" * 80)
    print("Testing 'Orden' search on both providers")
    print("=" * 80)
    
    # Test militaria321
    print("\n1. MILITARIA321.COM")
    print("-" * 80)
    m321 = Militaria321Provider()
    result_m321 = await m321.search("Orden", sample_mode=True)
    
    print(f"Total count: {result_m321.total_count}")
    print(f"Items found: {len(result_m321.items)}")
    print(f"Has more: {result_m321.has_more}")
    
    if result_m321.items:
        print(f"\nFirst 3 items:")
        for i, item in enumerate(result_m321.items[:3], 1):
            print(f"  {i}. {item.title}")
            print(f"     URL: {item.url}")
            print(f"     ID: {item.platform_id}")
    else:
        print("  NO ITEMS FOUND - This is the problem!")
    
    # Test egun
    print("\n2. EGUN.DE")
    print("-" * 80)
    egun = EgunProvider()
    result_egun = await egun.search("Orden", sample_mode=True)
    
    print(f"Total count: {result_egun.total_count}")
    print(f"Items found: {len(result_egun.items)}")
    print(f"Has more: {result_egun.has_more}")
    
    if result_egun.items:
        print(f"\nFirst 3 items:")
        for i, item in enumerate(result_egun.items[:3], 1):
            print(f"  {i}. {item.title}")
            print(f"     URL: {item.url}")
            print(f"     ID: {item.platform_id}")

if __name__ == "__main__":
    asyncio.run(test_orden())
