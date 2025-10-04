import asyncio
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional, List
from urllib.parse import urljoin, parse_qs, urlparse
import unicodedata

import httpx
from bs4 import BeautifulSoup
from dateutil import tz

from models import Listing, SearchResult
from providers.base import BaseProvider

logger = logging.getLogger(__name__)


class Militaria321Provider(BaseProvider):
    """Provider for militaria321.com with realistic headers and German parsing"""
    
    BASE_URL = "https://www.militaria321.com"
    SEARCH_URL = "https://www.militaria321.com/suchergebnisse.cfm"
    
    def __init__(self):
        # Realistic HTTP headers as specified
        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Encoding": "br, gzip, deflate",  # Specified in requirements
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",  # German as specified
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        
        # Berlin timezone for posted_ts parsing
        self.berlin_tz = tz.gettz('Europe/Berlin')
    
    @property
    def platform_name(self) -> str:
        return "militaria321.com"
    
    async def search(
        self, 
        keyword: str, 
        since_ts: Optional[datetime] = None,
        sample_mode: bool = False,
        crawl_all: bool = False
    ) -> SearchResult:
        """Search militaria321.com with proper pagination"""
        
        all_items = []
        pages_scanned = 0
        total_count = None
        
        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=30.0,
            follow_redirects=True
        ) as client:
            
            page_index = 1
            groupsize = 25  # Default page size
            
            while True:
                # Calculate pagination: startat = (page_index - 1) * groupsize + 1
                startat = (page_index - 1) * groupsize + 1
                
                params = {
                    "q": keyword,
                    "adv": "0",
                    "searchcat": "1",
                    "groupsize": str(groupsize),
                    "startat": str(startat)
                }
                
                try:
                    response = await client.get(self.SEARCH_URL, params=params)
                    response.raise_for_status()
                    
                    # Force UTF-8 if site advertises ISO-8859-1
                    if "iso-8859-1" in response.headers.get("content-type", "").lower():
                        response.encoding = "utf-8"
                    
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Log page info
                    logger.info({
                        "event": "m321_page",
                        "q": keyword,
                        "page_index": page_index,
                        "startat": startat,
                        "url": str(response.url)
                    })
                    
                    # Parse items from current page
                    page_items = self._parse_items_from_page(soup)
                    pages_scanned += 1
                    
                    print(f"DEBUG: Page {page_index} raw items: {len(page_items)}")
                    
                    logger.info({
                        "event": "m321_page",
                        "q": keyword,
                        "page_index": page_index,
                        "startat": startat,
                        "items_on_page": len(page_items),
                        "url": str(response.url)
                    })
                    
                    if not page_items:
                        break
                    
                    # Filter items with title-only matching
                    matched_items = []
                    for item in page_items:
                        if self._matches_keyword(item.title, keyword):
                            matched_items.append(item)
                    
                    all_items.extend(matched_items)
                    
                    # Early stop for polling mode if all items are too old
                    if not crawl_all and since_ts:
                        all_old = True
                        for item in matched_items:
                            if item.posted_ts is None or item.posted_ts >= since_ts:
                                all_old = False
                                break
                        if all_old:
                            logger.info(f"Early stop: all items on page {page_index} are older than since_ts")
                            break
                    
                    # Extract total count from first page
                    if page_index == 1:
                        total_count = self._extract_total_count(soup, len(page_items))
                    
                    # Check if we should continue to next page
                    if not crawl_all:
                        # For polling, only check first page
                        break
                    
                    # For crawl_all, continue based on pagination
                    if total_count and len(all_items) >= total_count:
                        break
                    
                    if len(page_items) < groupsize:
                        # Last page
                        break
                    
                    # Polite delay between pages
                    await asyncio.sleep(0.4)  # 400ms delay
                    page_index += 1
                    
                except Exception as e:
                    logger.error(f"Error fetching page {page_index}: {e}")
                    break
        
        # Enrich with posted_ts for unseen items (if needed)
        # This is handled by SearchService for militaria321 items
        
        return SearchResult(
            items=all_items,
            total_count=total_count or len(all_items),
            has_more=False,  # We crawled all available
            pages_scanned=pages_scanned
        )
    
    def _parse_items_from_page(self, soup: BeautifulSoup) -> List[Listing]:
        """Parse items from militaria321.com search results page"""
        items = []
        
        # Find auction links containing "auktion/" (with or without leading slash)
        auction_links = soup.find_all('a', href=re.compile(r'auktion/'))
        
        for link in auction_links:
            try:
                # Use parent container for full item info
                container = link.parent
                if not container:
                    continue
                
                # Extract URL and platform_id
                relative_url = link.get('href')
                if not relative_url:
                    continue
                
                full_url = urljoin(self.BASE_URL, relative_url)
                
                # Extract platform_id from URL: auktion/<id>/... or /auktion/<id>/...
                match = re.search(r'auktion/([^/]+)', relative_url)
                if not match:
                    continue
                
                platform_id = match.group(1)
                
                # Extract title (from link text)
                title = link.get_text(strip=True)
                if not title:
                    continue
                
                # Extract price (look in container)
                price_value, price_currency = self._parse_price_from_container(container)
                
                # Extract image URL if available
                image_url = self._extract_image_url(container)
                
                item = Listing(
                    platform="militaria321.com",
                    platform_id=platform_id,
                    title=title,
                    url=full_url,
                    price_value=price_value,
                    price_currency=price_currency,
                    image_url=image_url,
                    posted_ts=None  # Will be enriched later if needed
                )
                
                items.append(item)
                
            except Exception as e:
                logger.error(f"Error parsing item: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        return items
    
    def _parse_price_from_container(self, container) -> tuple[Optional[float], Optional[str]]:
        """Extract price from item container"""
        try:
            # Look for Euro price patterns
            price_text = container.get_text()
            
            # German price format: "249,00 €" or "1.234,56 €"
            price_matches = re.findall(r'([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})??)\s*€', price_text)
            
            if price_matches:
                price_str = price_matches[0]
                # Convert German format to Decimal
                # Remove thousand separators (dots), replace decimal comma with dot
                normalized = price_str.replace('.', '').replace(',', '.')
                price_value = float(Decimal(normalized))
                return price_value, "EUR"
            
        except (InvalidOperation, ValueError, AttributeError):
            pass
        
        return None, None
    
    def _extract_image_url(self, container) -> Optional[str]:
        """Extract image URL from container"""
        try:
            img = container.find('img')
            if img and img.get('src'):
                return urljoin(self.BASE_URL, img['src'])
        except Exception:
            pass
        return None
    
    def _extract_total_count(self, soup: BeautifulSoup, items_on_page: int) -> Optional[int]:
        """Extract total result count from search page"""
        try:
            # Look for result count indicators
            text = soup.get_text()
            
            # Pattern: "X Treffer gefunden" or similar
            match = re.search(r'([0-9]+)\s+(?:Treffer|Ergebnis)', text, re.IGNORECASE)
            if match:
                return int(match.group(1))
            
            # Fallback: use items on first page as minimum
            return items_on_page
            
        except Exception:
            return items_on_page
    
    def _matches_keyword(self, title: str, keyword: str) -> bool:
        """Title-only keyword matching with Unicode NFKC normalization
        
        Avoids timestamp false positives like '07:39 Uhr'
        """
        try:
            # Unicode NFKC normalization as specified
            norm_title = unicodedata.normalize('NFKC', title.lower())
            norm_keyword = unicodedata.normalize('NFKC', keyword.lower())
            
            # Whole-word matching to avoid timestamp false positives
            pattern = r'\b' + re.escape(norm_keyword) + r'\b'
            
            # Check if keyword matches
            if re.search(pattern, norm_title, re.IGNORECASE):
                # Additional check: avoid timestamp patterns like "XX:XX Uhr"
                if norm_keyword == 'uhr':
                    # Check if "uhr" is preceded by time pattern
                    time_pattern = r'\b\d{1,2}:\d{2}\s+uhr\b'
                    if re.search(time_pattern, norm_title):
                        return False  # Skip timestamp matches
                
                return True
            
        except Exception as e:
            logger.warning(f"Error in keyword matching: {e}")
        
        return False
    
    def format_price_de(self, price_value: Optional[float], currency: Optional[str] = None) -> str:
        """Format price in German format as specified"""
        if price_value is None:
            return "/"
        
        try:
            # German number format: 1.234,56 €
            if price_value >= 1000:
                # Add thousand separators
                euros = int(price_value)
                cents = int((price_value - euros) * 100)
                
                # Format with thousand separators (dots)
                euro_str = f"{euros:,}".replace(',', '.')
                price_formatted = f"{euro_str},{cents:02d}"
            else:
                price_formatted = f"{price_value:.2f}".replace('.', ',')
            
            currency_symbol = "€" if currency == "EUR" else (currency or "€")
            return f"{price_formatted} {currency_symbol}"
            
        except Exception:
            return "/"
    
    async def fetch_posted_ts_batch(self, items: List[Listing], concurrency: int = 4):
        """Fetch posted_ts from detail pages for multiple items
        
        Only fetches for items that don't already have posted_ts
        """
        items_to_fetch = [item for item in items if item.posted_ts is None]
        
        if not items_to_fetch:
            return
        
        semaphore = asyncio.Semaphore(concurrency)
        
        async def fetch_one(item: Listing):
            async with semaphore:
                try:
                    posted_ts = await self._fetch_posted_ts_from_detail_page(item.url)
                    item.posted_ts = posted_ts
                except Exception as e:
                    logger.warning(f"Failed to fetch posted_ts for {item.url}: {e}")
        
        # Process in batches
        tasks = [fetch_one(item) for item in items_to_fetch]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _fetch_posted_ts_from_detail_page(self, url: str) -> Optional[datetime]:
        """Fetch posted timestamp from item detail page"""
        async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                
                if "iso-8859-1" in response.headers.get("content-type", "").lower():
                    response.encoding = "utf-8"
                
                soup = BeautifulSoup(response.text, 'html.parser')
                return self._parse_posted_ts_from_html(soup)
                
            except Exception as e:
                logger.warning(f"Error fetching detail page {url}: {e}")
                return None
    
    def _parse_posted_ts_from_html(self, soup: BeautifulSoup) -> Optional[datetime]:
        """Parse posted timestamp from German HTML
        
        Looks for patterns like:
        - "Auktionsbeginn: 04.10.2025 13:21 Uhr"
        - "Eingestellt: 04.10.2025 13:21 Uhr"
        """
        try:
            text = soup.get_text()
            
            # German date patterns
            patterns = [
                r'(?:Auktionsbeginn|Eingestellt)\s*:?\s*([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4})\s+([0-9]{1,2}:[0-9]{2})\s+Uhr',
                r'([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4})\s+([0-9]{1,2}:[0-9]{2})\s+Uhr',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    date_str = match.group(1)  # "04.10.2025"
                    time_str = match.group(2)  # "13:21"
                    
                    # Parse German date format
                    dt_str = f"{date_str} {time_str}"
                    dt_berlin = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
                    
                    # Convert from Berlin timezone to UTC
                    dt_berlin = dt_berlin.replace(tzinfo=self.berlin_tz)
                    dt_utc = dt_berlin.astimezone(timezone.utc).replace(tzinfo=None)
                    
                    return dt_utc
            
        except Exception as e:
            logger.warning(f"Error parsing posted_ts: {e}")
        
        return None
