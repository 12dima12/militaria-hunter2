#!/usr/bin/env python3
"""
Save search HTML to file for inspection
"""
import asyncio
import httpx

async def save_html():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate',
    }
    
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
        response = await client.get("https://www.militaria321.com/search.cfm", params={'q': 'Brieföffner'})
        
        with open('/app/briefoffner_search.html', 'w', encoding='utf-8') as f:
            f.write(response.text)
        
        print("Saved to /app/briefoffner_search.html")
        
        # Find first auction link and print surrounding HTML
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')
        
        auction_links = soup.find_all('a', href=lambda x: x and 'auktion' in str(x).lower())
        
        if auction_links:
            first_link = auction_links[0]
            print(f"\nFirst auction link: {first_link.get('href')}")
            print(f"Link text: {first_link.get_text().strip()}")
            
            # Get parent
            parent = first_link.find_parent('tr')
            if not parent:
                parent = first_link.find_parent('td')
            if not parent:
                parent = first_link.find_parent('div')
            
            print(f"\nParent tag: {parent.name if parent else 'None'}")
            if parent:
                print(f"\nParent HTML (first 500 chars):\n{str(parent)[:500]}")

asyncio.run(save_html())
