import httpx
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
import logging
import asyncio
from urllib.parse import urljoin, quote_plus
import re

from .base import BaseProvider
from models import Listing

logger = logging.getLogger(__name__)


class Militaria321Provider(BaseProvider):
    """Provider for militaria321.com"""
    
    def __init__(self):
        super().__init__("militaria321.com")
        self.base_url = "https://www.militaria321.com"
        self.search_url = f"{self.base_url}/search"
        
    async def search(self, keyword: str, since_ts: Optional[datetime] = None) -> List[Listing]:
        """Search militaria321.com for listings"""
        try:
            query = self.build_query(keyword)
            listings = await self._fetch_listings(query)
            
            # Filter by timestamp if provided
            if since_ts:
                # For now, we'll return all listings since militaria321 doesn't have timestamp filtering
                # In production, you'd store last_checked timestamps and compare
                pass
            
            return listings
            
        except Exception as e:
            logger.error(f"Error searching militaria321 for '{keyword}': {e}")
            return []
    
    async def _fetch_listings(self, query: str) -> List[Listing]:
        """Fetch listings from militaria321.com search"""
        listings = []
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,de;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        try:
            # Build search URL with query
            search_params = f"?q={quote_plus(query)}"
            url = f"{self.search_url}{search_params}"
            
            async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                logger.info(f"Fetching militaria321 search: {url}")
                response = await client.get(url)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.content, 'html.parser')
                listings = self._parse_listings(soup, query)
                
                logger.info(f"Found {len(listings)} listings for '{query}'")
                return listings
                
        except httpx.RequestError as e:
            logger.error(f"Request error fetching militaria321: {e}")
            return []
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching militaria321: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching militaria321: {e}")
            return []
    
    def _parse_listings(self, soup: BeautifulSoup, original_query: str) -> List[Listing]:
        """Parse listings from militaria321.com search results"""
        listings = []
        
        try:
            # This is a generic parser - militaria321 structure may vary
            # Look for common listing patterns
            listing_selectors = [
                '.listing-item',
                '.product-item', 
                '.search-result',
                'article',
                '.item',
                '[data-product-id]'
            ]
            
            listing_elements = []
            for selector in listing_selectors:
                elements = soup.select(selector)
                if elements:
                    listing_elements = elements
                    logger.info(f"Using selector '{selector}' - found {len(elements)} elements")
                    break
            
            if not listing_elements:
                # Fallback: look for any links that might be listings
                listing_elements = soup.select('a[href*="/item/"], a[href*="/product/"], a[href*="/listing/"]')
                logger.info(f"Fallback: found {len(listing_elements)} potential listing links")
            
            for element in listing_elements[:20]:  # Limit to 20 results
                try:
                    listing = self._parse_single_listing(element, original_query)
                    if listing:
                        listings.append(listing)
                except Exception as e:
                    logger.debug(f"Error parsing single listing: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error parsing militaria321 listings: {e}")
        
        return listings
    
    def _parse_single_listing(self, element, original_query: str) -> Optional[Listing]:
        """Parse a single listing element"""
        try:
            # Extract title
            title_selectors = ['h2', 'h3', '.title', '.name', '.product-title', 'a']
            title = None
            for selector in title_selectors:
                title_elem = element.select_one(selector)
                if title_elem and title_elem.get_text().strip():
                    title = title_elem.get_text().strip()[:200]  # Limit length
                    break
            
            if not title:
                return None
            
            # Extract URL
            url = None
            link = element.find('a')
            if link and link.get('href'):
                url = urljoin(self.base_url, link['href'])
            elif element.name == 'a' and element.get('href'):
                url = urljoin(self.base_url, element['href'])
            
            if not url:
                return None
            
            # Extract price
            price_value, price_currency = self._extract_price(element)
            
            # Extract image
            image_url = self._extract_image(element)
            
            # Extract other details
            location = self._extract_location(element)
            condition = self._extract_condition(element)
            seller_name = self._extract_seller(element)
            
            # Generate platform_id from URL
            platform_id = self._extract_platform_id(url)
            
            return Listing(
                platform=self.name,
                platform_id=platform_id,
                title=title,
                url=url,
                price_value=price_value,
                price_currency=price_currency,
                location=location,
                condition=condition,
                seller_name=seller_name,
                image_url=image_url,
                first_seen_ts=datetime.utcnow(),
                last_seen_ts=datetime.utcnow()
            )
            
        except Exception as e:
            logger.debug(f"Error parsing single listing element: {e}")
            return None
    
    def _extract_price(self, element):
        """Extract price from listing element"""
        price_selectors = ['.price', '.cost', '.amount', '[class*="price"]', '[id*="price"]']
        price_text = None
        
        for selector in price_selectors:
            price_elem = element.select_one(selector)
            if price_elem:
                price_text = price_elem.get_text().strip()
                break
        
        if not price_text:
            # Look for currency symbols in any text
            text = element.get_text()
            price_match = re.search(r'([\d,]+(?:\.\d{2})?)\s*([€$£]|EUR|USD|GBP)', text)
            if price_match:
                price_text = price_match.group()
        
        if price_text:
            # Parse price
            price_match = re.search(r'([\d,]+(?:\.\d{2})?)', price_text.replace(',', ''))
            currency_match = re.search(r'([€$£]|EUR|USD|GBP)', price_text)
            
            price_value = None
            if price_match:
                try:
                    price_value = float(price_match.group(1))
                except ValueError:
                    pass
            
            price_currency = None
            if currency_match:
                currency_map = {'€': 'EUR', '$': 'USD', '£': 'GBP'}
                currency = currency_match.group(1)
                price_currency = currency_map.get(currency, currency)
            
            return price_value, price_currency
        
        return None, None
    
    def _extract_image(self, element):
        """Extract image URL from listing element"""
        img = element.find('img')
        if img:
            src = img.get('src') or img.get('data-src')
            if src:
                return urljoin(self.base_url, src)
        return None
    
    def _extract_location(self, element):
        """Extract location from listing element"""
        location_selectors = ['.location', '.address', '[class*="location"]', '[class*="city"]']
        for selector in location_selectors:
            elem = element.select_one(selector)
            if elem:
                return elem.get_text().strip()[:100]
        return None
    
    def _extract_condition(self, element):
        """Extract condition from listing element"""
        condition_keywords = ['neu', 'gebraucht', 'new', 'used', 'excellent', 'good', 'fair', 'poor']
        text = element.get_text().lower()
        for keyword in condition_keywords:
            if keyword in text:
                return keyword.capitalize()
        return None
    
    def _extract_seller(self, element):
        """Extract seller name from listing element"""
        seller_selectors = ['.seller', '.vendor', '.shop', '[class*="seller"]']
        for selector in seller_selectors:
            elem = element.select_one(selector)
            if elem:
                return elem.get_text().strip()[:50]
        return None
    
    def _extract_platform_id(self, url: str) -> str:
        """Extract unique ID from URL"""
        # Try to find ID patterns in URL
        patterns = [
            r'/item/(\d+)',
            r'/product/(\d+)', 
            r'/listing/(\d+)',
            r'id=(\d+)',
            r'item_id=(\d+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # Fallback: use URL hash
        return str(hash(url))
    
    def build_query(self, keyword: str) -> str:
        """Build militaria321-specific search query"""
        # Simple query normalization
        return keyword.strip()