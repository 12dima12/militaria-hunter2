#!/usr/bin/env python3
"""
Inspect egun.de search form structure
"""
import httpx
from bs4 import BeautifulSoup
import asyncio

async def inspect_egun_search():
    """Inspect the search form on egun.de"""
    
    base_url = "https://www.egun.de"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
    }
    
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
        # Check the homepage for search form
        print("=" * 80)
        print("1. Fetching homepage to find search form...")
        print("=" * 80)
        response = await client.get(base_url)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        print(f"Status: {response.status_code}")
        print(f"Final URL: {response.url}")
        
        # Find all forms
        forms = soup.find_all('form')
        print(f"\nFound {len(forms)} forms on homepage")
        
        for i, form in enumerate(forms):
            print(f"\n--- Form {i+1} ---")
            print(f"Action: {form.get('action')}")
            print(f"Method: {form.get('method', 'GET')}")
            
            # Find all input fields
            inputs = form.find_all(['input', 'select', 'textarea'])
            print(f"Input fields: {len(inputs)}")
            for inp in inputs:
                inp_type = inp.get('type', inp.name)
                inp_name = inp.get('name')
                inp_value = inp.get('value', '')
                inp_id = inp.get('id', '')
                if inp_name:  # Only show fields with names
                    print(f"  - [{inp_type}] name='{inp_name}', value='{inp_value}', id='{inp_id}'")
        
        # Try to find a search page
        print("\n" + "=" * 80)
        print("2. Looking for search page...")
        print("=" * 80)
        
        # Common search page URLs
        search_urls = [
            f"{base_url}/search",
            f"{base_url}/suche",
            f"{base_url}/markt/suche",
            f"{base_url}/market/search",
        ]
        
        for search_url in search_urls:
            try:
                response = await client.get(search_url)
                if response.status_code == 200:
                    print(f"\n✓ Found search page: {search_url}")
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    forms = soup.find_all('form')
                    print(f"Found {len(forms)} forms on search page")
                    
                    for i, form in enumerate(forms[:2]):  # Show first 2 forms
                        print(f"\n--- Form {i+1} ---")
                        print(f"Action: {form.get('action')}")
                        print(f"Method: {form.get('method', 'GET')}")
                        
                        inputs = form.find_all(['input', 'select', 'textarea'])
                        for inp in inputs:
                            inp_type = inp.get('type', inp.name)
                            inp_name = inp.get('name')
                            inp_value = inp.get('value', '')
                            if inp_name:
                                print(f"  - [{inp_type}] name='{inp_name}', value='{inp_value}'")
                    break
            except Exception as e:
                print(f"  ✗ {search_url}: {e}")
        
        # Try an actual search
        print("\n" + "=" * 80)
        print("3. Testing actual search with 'Büchse'...")
        print("=" * 80)
        
        # Try different parameter combinations
        test_params = [
            {"q": "Büchse"},
            {"query": "Büchse"},
            {"search": "Büchse"},
            {"keyword": "Büchse"},
            {"searchword": "Büchse"},
            {"s": "Büchse"},
        ]
        
        for params in test_params:
            print(f"\nTrying params: {params}")
            try:
                response = await client.get(f"{base_url}/markt/", params=params)
                
                # Check if search was reflected
                soup = BeautifulSoup(response.text, 'html.parser')
                page_text = soup.get_text().lower()
                
                # Look for the search term in the response
                if "büchse" in page_text:
                    print(f"  ✓ Search term found in response!")
                    print(f"  Final URL: {response.url}")
                    
                    # Look for result indicators
                    if any(word in page_text for word in ['treffer', 'result', 'angebot']):
                        print(f"  ✓ Result indicators found!")
                    
                    # Look for listing links
                    links = soup.find_all('a', href=True)
                    article_links = [l for l in links if 'artikel' in l.get('href', '').lower() or 'item' in l.get('href', '').lower()]
                    print(f"  Found {len(article_links)} potential listing links")
                    
                    if article_links:
                        print(f"  Sample links:")
                        for link in article_links[:3]:
                            print(f"    - {link.get('href')} | {link.get_text().strip()[:50]}")
                    
                    print(f"  >>> THIS PARAMETER WORKS: {params}")
                    break
                else:
                    print(f"  ✗ Search term NOT found in response")
            except Exception as e:
                print(f"  ✗ Error: {e}")

if __name__ == "__main__":
    asyncio.run(inspect_egun_search())
