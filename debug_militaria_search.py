#!/usr/bin/env python3
"""
Debug militaria321 search to see what's happening
"""

import asyncio
import httpx
from bs4 import BeautifulSoup

async def debug_search():
    """Debug the militaria321 search"""
    
    base_url = "https://www.militaria321.com"
    search_url = "https://www.militaria321.com/suchergebnisse.cfm"
    
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    
    params = {
        "q": "orden",
        "adv": "0",
        "searchcat": "1",
        "groupsize": "25",
        "startat": "1"
    }
    
    print("=== Debugging Militaria321 Search ===\n")
    print(f"URL: {search_url}")
    print(f"Params: {params}")
    print(f"Headers: {headers}")
    
    async with httpx.AsyncClient(
        headers=headers,
        timeout=30.0,
        follow_redirects=True
    ) as client:
        
        try:
            print("\nMaking request...")
            response = await client.get(search_url, params=params)
            
            print(f"Status: {response.status_code}")
            print(f"URL: {response.url}")
            print(f"Content-Type: {response.headers.get('content-type')}")
            print(f"Content-Length: {len(response.text)}")
            
            if "iso-8859-1" in response.headers.get("content-type", "").lower():
                response.encoding = "utf-8"
            
            # Check if we got a proper response
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for search results
                print(f"\nHTML title: {soup.title.string if soup.title else 'No title'}")
                
                # Check for auction links
                auction_links = soup.find_all('a', href=lambda x: x and 'auktion' in x.lower())
                print(f"Found {len(auction_links)} auction links")
                
                if auction_links:
                    for i, link in enumerate(auction_links[:5]):
                        print(f"  Link {i+1}: {link.get('href', 'No href')}")
                        print(f"    Text: {link.get_text()[:50]}...")
                
                # Look for result count indicators
                text_content = soup.get_text()
                if "treffer" in text_content.lower():
                    print(f"\n'Treffer' found in content")
                if "auktion" in text_content.lower():
                    print(f"'Auktion' found in content")
                    
                # Check for error messages or redirects
                if "fehler" in text_content.lower():
                    print("WARNING: 'Fehler' found in content")
                if "error" in text_content.lower():
                    print("WARNING: 'Error' found in content")
                    
                # Save a snippet of the HTML for inspection
                print(f"\nFirst 500 chars of HTML:")
                print(response.text[:500])
                
                # Check for search form to understand the correct parameters
                forms = soup.find_all('form')
                print(f"\nFound {len(forms)} forms")
                for i, form in enumerate(forms):
                    print(f"  Form {i+1}: action='{form.get('action')}' method='{form.get('method')}'")
                    inputs = form.find_all(['input', 'select'])
                    for inp in inputs[:5]:  # First 5 inputs
                        print(f"    {inp.name}: {inp.get('name')} = {inp.get('value', inp.get('selected', 'no value'))}")
                
            else:
                print(f"HTTP Error: {response.status_code}")
                print(f"Response: {response.text[:200]}")
                
        except Exception as e:
            print(f"Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(debug_search())