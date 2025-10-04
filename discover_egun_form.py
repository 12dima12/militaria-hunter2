#!/usr/bin/env python3
"""
Discover egun.de search form at /market/index.php
"""
import httpx
from bs4 import BeautifulSoup
import asyncio
from urllib.parse import urljoin, quote_plus

async def discover_egun_form():
    """Discover the search form structure"""
    
    entry_url = "https://www.egun.de/market/index.php"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
    }
    
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
        print("=" * 80)
        print(f"1. Fetching {entry_url}")
        print("=" * 80)
        
        response = await client.get(entry_url)
        print(f"Status: {response.status_code}")
        print(f"Final URL: {response.url}")
        print(f"Encoding: {response.encoding}")
        
        # Explicitly set encoding
        if response.encoding is None:
            response.encoding = response.apparent_encoding
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all forms
        forms = soup.find_all('form')
        print(f"\nFound {len(forms)} forms")
        
        for i, form in enumerate(forms):
            print(f"\n{'=' * 60}")
            print(f"Form {i+1}")
            print(f"{'=' * 60}")
            print(f"Action: {form.get('action')}")
            print(f"Method: {form.get('method', 'GET').upper()}")
            print(f"ID: {form.get('id', 'N/A')}")
            print(f"Name: {form.get('name', 'N/A')}")
            
            # Find all input fields
            inputs = form.find_all(['input', 'select', 'textarea', 'button'])
            print(f"\nFields ({len(inputs)}):")
            
            for inp in inputs:
                tag = inp.name
                inp_type = inp.get('type', tag)
                inp_name = inp.get('name', '')
                inp_value = inp.get('value', '')
                inp_id = inp.get('id', '')
                inp_placeholder = inp.get('placeholder', '')
                
                if inp_name or inp_type == 'submit':
                    print(f"  [{tag}/{inp_type}] name='{inp_name}', value='{inp_value}', id='{inp_id}', placeholder='{inp_placeholder}'")
                    
                    # Check if this looks like a search field
                    search_indicators = ['search', 'suche', 'query', 'q', 'keyword', 'text']
                    if any(ind in inp_name.lower() or ind in inp_placeholder.lower() or ind in inp_id.lower() for ind in search_indicators):
                        print(f"      ^^^ LIKELY SEARCH FIELD ^^^")
        
        # Try to identify the most likely search form
        print("\n" + "=" * 80)
        print("2. Identifying Search Form")
        print("=" * 80)
        
        # Look for forms with search-related inputs
        for i, form in enumerate(forms):
            inputs = form.find_all('input')
            search_inputs = []
            
            for inp in inputs:
                inp_name = inp.get('name', '').lower()
                inp_type = inp.get('type', '').lower()
                inp_placeholder = inp.get('placeholder', '').lower()
                inp_id = inp.get('id', '').lower()
                
                # Check for search indicators
                if any(term in inp_name or term in inp_placeholder or term in inp_id 
                       for term in ['search', 'suche', 'query', 'q', 'find']):
                    search_inputs.append({
                        'name': inp.get('name'),
                        'type': inp.get('type', 'text'),
                        'placeholder': inp.get('placeholder'),
                        'id': inp.get('id')
                    })
            
            if search_inputs:
                print(f"\n✓ Form {i+1} appears to be a search form:")
                print(f"  Action: {form.get('action')}")
                print(f"  Method: {form.get('method', 'GET').upper()}")
                print(f"  Search fields found: {len(search_inputs)}")
                for field in search_inputs:
                    print(f"    - name='{field['name']}', type={field['type']}, placeholder='{field['placeholder']}'")
                
                # Build a test URL
                action = form.get('action', '')
                if action:
                    full_action = urljoin(str(response.url), action)
                    print(f"  Full action URL: {full_action}")
        
        # Try a test search
        print("\n" + "=" * 80)
        print("3. Testing Search with 'Büchse'")
        print("=" * 80)
        
        # Try common patterns based on discovered forms
        test_keyword = "Büchse"
        
        # Pattern 1: Direct GET with common params
        test_urls = [
            f"{entry_url}?search={quote_plus(test_keyword)}",
            f"{entry_url}?q={quote_plus(test_keyword)}",
            f"{entry_url}?keyword={quote_plus(test_keyword)}",
            f"{entry_url}?searchword={quote_plus(test_keyword)}",
        ]
        
        for test_url in test_urls:
            print(f"\nTrying: {test_url}")
            response = await client.get(test_url, headers={'Referer': entry_url})
            
            if response.encoding is None:
                response.encoding = response.apparent_encoding
            
            soup = BeautifulSoup(response.text, 'html.parser')
            page_text = soup.get_text().lower()
            
            # Check if keyword appears
            if "büchse" in page_text:
                print(f"  ✓ Keyword found in response!")
                print(f"  Final URL: {response.url}")
                
                # Look for result indicators
                result_words = ['treffer', 'ergebnis', 'result', 'angebot', 'artikel']
                found_indicators = [w for w in result_words if w in page_text]
                if found_indicators:
                    print(f"  ✓ Result indicators: {found_indicators}")
                
                # Look for item links
                links = soup.find_all('a', href=True)
                item_links = [l for l in links if 'item' in l.get('href', '').lower() or 'artikel' in l.get('href', '').lower()]
                print(f"  Found {len(item_links)} potential item links")
                
                if item_links:
                    print(f"  Sample links:")
                    for link in item_links[:3]:
                        print(f"    {link.get('href')} | {link.get_text().strip()[:50]}")
                
                print(f"\n  >>> THIS WORKS! <<<")
                break
            else:
                print(f"  ✗ Keyword not found")

if __name__ == "__main__":
    asyncio.run(discover_egun_form())
