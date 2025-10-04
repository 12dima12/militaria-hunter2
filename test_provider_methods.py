#!/usr/bin/env python3
"""
Test script to inspect available methods in militaria321 provider
"""
import sys
sys.path.insert(0, '/app/backend')

from providers.militaria321 import Militaria321Provider
import inspect

def inspect_provider():
    """Inspect the militaria321 provider class"""
    
    print("=" * 80)
    print("MILITARIA321 PROVIDER METHOD INSPECTION")
    print("=" * 80)
    
    provider = Militaria321Provider()
    
    print(f"Provider name: {provider.name}")
    print(f"Base URL: {getattr(provider, 'base_url', 'NOT SET')}")
    print(f"Search URL: {getattr(provider, 'search_url', 'NOT SET')}")
    
    print("\n" + "=" * 40)
    print("AVAILABLE METHODS:")
    print("=" * 40)
    
    methods = [method for method in dir(provider) if not method.startswith('__')]
    for method in sorted(methods):
        attr = getattr(provider, method)
        if callable(attr):
            sig = inspect.signature(attr) if hasattr(inspect, 'signature') else "(...)"
            print(f"✓ {method}{sig}")
        else:
            print(f"  {method} = {attr}")
    
    print("\n" + "=" * 40)
    print("MISSING CRITICAL METHODS:")
    print("=" * 40)
    
    critical_methods = [
        '_fetch_page',
        'build_query', 
        '_parse_posted_ts_from_text',
        'fetch_posted_ts_batch'
    ]
    
    for method in critical_methods:
        if hasattr(provider, method):
            print(f"✓ {method} - FOUND")
        else:
            print(f"✗ {method} - MISSING")
    
    print("\n" + "=" * 40)
    print("TESTING BASIC FUNCTIONALITY:")
    print("=" * 40)
    
    # Test build_query (should be inherited from base)
    try:
        query = provider.build_query("Brieföffner")
        print(f"✓ build_query('Brieföffner') = '{query}'")
    except Exception as e:
        print(f"✗ build_query failed: {e}")
    
    # Test matches_keyword
    try:
        result = provider.matches_keyword("Wehrmacht Brieföffner", "brieföffner")
        print(f"✓ matches_keyword('Wehrmacht Brieföffner', 'brieföffner') = {result}")
    except Exception as e:
        print(f"✗ matches_keyword failed: {e}")
    
    # Test parse_price
    try:
        price, currency = provider.parse_price("249,00 €")
        print(f"✓ parse_price('249,00 €') = ({price}, '{currency}')")
    except Exception as e:
        print(f"✗ parse_price failed: {e}")
    
    # Test format_price_de
    try:
        from decimal import Decimal
        formatted = provider.format_price_de(Decimal("249.00"), "EUR")
        print(f"✓ format_price_de(249.00, 'EUR') = '{formatted}'")
    except Exception as e:
        print(f"✗ format_price_de failed: {e}")

if __name__ == "__main__":
    inspect_provider()