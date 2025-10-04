#!/usr/bin/env python3
"""
Test script to inspect militaria321.com search form structure
"""
import httpx
from bs4 import BeautifulSoup
import asyncio

async def inspect_search_form():
    """Inspect the search form on militaria321.com"""
    
    # First, get the main search page
    base_url = "https://www.militaria321.com"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate',
    }
    
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
        # Check the homepage for search form
        print("=" * 80)
        print("1. Fetching homepage to find search form...")
        print("=" * 80)
        response = await client.get(base_url)
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
                print(f"  - {inp.name}: name={inp.get('name')}, type={inp.get('type')}, value={inp.get('value')}")
        
        # Now try the search page directly
        print("\n" + "=" * 80)
        print("2. Fetching search.cfm page...")
        print("=" * 80)
        search_url = f"{base_url}/search.cfm"
        response = await client.get(search_url)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        forms = soup.find_all('form')
        print(f"\nFound {len(forms)} forms on search page")
        
        for i, form in enumerate(forms):
            print(f"\n--- Form {i+1} ---")
            print(f"Action: {form.get('action')}")
            print(f"Method: {form.get('method', 'GET')}")
            
            inputs = form.find_all(['input', 'select', 'textarea'])
            print(f"Input fields: {len(inputs)}")
            for inp in inputs:
                inp_type = inp.get('type', inp.name)
                inp_name = inp.get('name')
                inp_value = inp.get('value', '')
                inp_id = inp.get('id', '')
                print(f"  - [{inp_type}] name='{inp_name}', value='{inp_value}', id='{inp_id}'")
        
        # Try an actual search to see what parameters are used
        print("\n" + "=" * 80)
        print("3. Testing actual search with 'Brieföffner'...")
        print("=" * 80)
        
        # Try different parameter combinations
        test_params = [
            {"wort": "Brieföffner"},
            {"q": "Brieföffner"},
            {"keyword": "Brieföffner"},
            {"search": "Brieföffner"},
            {"text": "Brieföffner"},
        ]
        
        for params in test_params:
            print(f"\nTrying params: {params}")
            response = await client.get(search_url, params=params)
            
            # Check if search was reflected
            soup = BeautifulSoup(response.text, 'html.parser')
            page_text = soup.get_text().lower()
            
            # Look for the search term in the response
            if "brieföffner" in page_text:
                print(f"  ✓ Search term found in response!")
                
                # Look for result indicators
                if "treffer" in page_text or "result" in page_text:
                    print(f"  ✓ Result indicators found!")
                    # Extract a snippet
                    lines = [l.strip() for l in page_text.split('\n') if 'treffer' in l.lower() or 'result' in l.lower()]
                    if lines:
                        print(f"  Result text: {lines[0][:200]}")
                
                # Look for auction links
                auction_links = soup.find_all('a', href=lambda x: x and 'auktion' in str(x).lower())
                print(f"  Found {len(auction_links)} auction-related links")
                
                # Show first few auction links
                for i, link in enumerate(auction_links[:5]):
                    href = link.get('href')
                    text = link.get_text().strip()
                    print(f"    {i+1}. {text[:60]} -> {href}")
                
                # This parameter works!
                print(f"\n  >>> THIS PARAMETER WORKS: {params}")
                
                # Try to find result containers
                print("\n  Looking for result containers...")
                
                # Try different selectors
                tr_with_bgcolor = soup.find_all('tr', bgcolor=True)
                print(f"    Found {len(tr_with_bgcolor)} <tr> with bgcolor")
                
                tr_with_auktion = soup.find_all('tr', string=lambda x: x and 'brieföffner' in str(x).lower())
                print(f"    Found {len(tr_with_auktion)} <tr> with 'brieföffner' in text")
                
                # Find all text containing "brieföffner"
                all_text_nodes = soup.find_all(string=lambda x: x and 'brieföffner' in str(x).lower())
                print(f"    Found {len(all_text_nodes)} text nodes with 'brieföffner'")
                
                break
            else:
                print(f"  ✗ Search term NOT found in response")

if __name__ == "__main__":
    asyncio.run(inspect_search_form())
