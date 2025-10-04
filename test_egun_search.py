#!/usr/bin/env python3
"""
Test egun.de search with discovered form parameters
"""
import httpx
from bs4 import BeautifulSoup
import asyncio
from urllib.parse import urljoin

async def test_egun_search():
    """Test search using discovered parameters"""
    
    base_url = "https://www.egun.de/market/"
    search_url = f"{base_url}list_items.php"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': f'{base_url}index.php'
    }
    
    # Test keywords
    test_keywords = ["Büchse", "Pistole", "Messer"]
    
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
        for keyword in test_keywords:
            print("\n" + "=" * 80)
            print(f"Testing: {keyword}")
            print("=" * 80)
            
            # Use discovered form parameters
            params = {
                'mode': 'qry',
                'plusdescr': 'off',
                'wheremode': 'and',
                'query': keyword,
                'quick': '1'
            }
            
            response = await client.get(search_url, params=params)
            print(f"URL: {response.url}")
            print(f"Status: {response.status_code}")
            print(f"Encoding: {response.encoding}")
            
            # Set encoding
            if response.encoding is None or response.encoding == 'ISO-8859-1':
                response.encoding = 'utf-8'
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Check if keyword reflected
            page_text = soup.get_text().lower()
            keyword_lower = keyword.lower()
            
            if keyword_lower in page_text:
                print(f"✓ Keyword '{keyword}' found in response")
            else:
                print(f"✗ Keyword '{keyword}' NOT found")
            
            # Look for result count
            count_text = soup.find(string=lambda x: x and ('treffer' in str(x).lower() or 'ergebnis' in str(x).lower()))
            if count_text:
                print(f"Result indicator: {count_text.strip()[:100]}")
            
            # Find item links
            # egun uses item.php?id=XXX pattern
            item_links = soup.find_all('a', href=lambda x: x and 'item.php' in str(x))
            print(f"Found {len(item_links)} item links")
            
            if item_links:
                print("\nFirst 3 items:")
                for i, link in enumerate(item_links[:3], 1):
                    href = link.get('href')
                    title = link.get_text().strip()
                    full_url = urljoin(str(response.url), href)
                    print(f"  {i}. {title[:60]}")
                    print(f"     {full_url}")
                    
                    # Try to find price in parent container
                    parent = link.find_parent('tr') or link.find_parent('td') or link.find_parent('div')
                    if parent:
                        parent_text = parent.get_text()
                        # Look for price patterns
                        import re
                        price_patterns = [
                            r'(\d+[.,]\d+)\s*€',
                            r'€\s*(\d+[.,]\d+)',
                            r'EUR\s*(\d+[.,]\d+)',
                            r'(\d+)\s*€'
                        ]
                        for pattern in price_patterns:
                            match = re.search(pattern, parent_text)
                            if match:
                                print(f"     Price: {match.group()}")
                                break

if __name__ == "__main__":
    asyncio.run(test_egun_search())
