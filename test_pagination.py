#!/usr/bin/env python3
"""
Test pagination detection
"""
import sys
import asyncio
sys.path.insert(0, '/app/backend')

import httpx
from bs4 import BeautifulSoup
from providers.egun import EgunProvider

async def test_egun_pagination():
    """Test egun pagination"""
    print("=" * 80)
    print("Testing egun.de pagination for 'Orden'")
    print("=" * 80)
    
    egun = EgunProvider()
    
    # Build first page URL
    params = {
        'mode': 'qry',
        'plusdescr': 'off',
        'wheremode': 'and',
        'query': 'Orden',
        'quick': '1'
    }
    
    from urllib.parse import urlencode
    url = f"{egun.search_url}?{urlencode(params)}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'text/html',
        'Accept-Language': 'de-DE,de;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
    }
    
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
        page_num = 1
        max_pages = 5
        
        while url and page_num <= max_pages:
            print(f"\nPage {page_num}")
            print(f"URL: {url}")
            
            response = await client.get(url)
            if response.encoding is None or response.encoding.lower() == 'iso-8859-1':
                response.encoding = 'utf-8'
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find item links
            item_links = soup.find_all('a', href=lambda x: x and 'item.php?id=' in str(x))
            print(f"Items on page: {len(item_links)}")
            
            # Get next page
            next_url = egun._get_next_page_url(url, soup)
            
            if next_url:
                print(f"Next page URL: {next_url}")
                url = next_url
                page_num += 1
            else:
                print("No next page found - stopping")
                break
        
        print(f"\nTotal pages crawled: {page_num}")

if __name__ == "__main__":
    asyncio.run(test_egun_pagination())
