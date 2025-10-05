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
            "Accept-Encoding": "gzip, deflate",  # Simplified for compatibility
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",  # German as specified
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
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
        crawl_all: bool = False,
        max_pages_override: Optional[int] = None,
        mode: Optional[str] = None,
        poll_pages: Optional[int] = None,
        page_start: Optional[int] = None
    ) -> SearchResult:
        """Search militaria321.com with proper pagination"""
        
        all_items = []
        pages_scanned = 0
        total_count = None
        seen_ids = set()  # Track IDs to avoid duplicates
        
        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=30.0,
            follow_redirects=True
        ) as client:
            
            page_index = 1
            groupsize = 25  # Default page size
            
            # Determine max pages to crawl
            if max_pages_override:
                max_pages = max_pages_override
            elif crawl_all:
                max_pages = 2000  # Full crawl for baseline
            else:
                max_pages = 1  # Default: first page only
            
            while page_index <= max_pages:
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
                    
                    if not page_items:
                        logger.info(f"No items found on page {page_index}, ending crawl")
                        break
                    
                    # Filter items with title-only matching and deduplicate by ID
                    matched_items = []
                    duplicates_on_page = 0
                    for item in page_items:
                        # Skip duplicates
                        if item.platform_id in seen_ids:
                            duplicates_on_page += 1
                            continue
                        
                        if self._matches_keyword(item.title, keyword):
                            matched_items.append(item)
                            seen_ids.add(item.platform_id)
                    
                    # Structured logging per page as specified
                    logger.info({
                        "event": "m321_page",
                        "q": keyword,
                        "page_index": page_index,
                        "items_on_page": len(page_items),
                        "duplicates_on_page": duplicates_on_page,
                        "total_matched_so_far": len(all_items) + len(matched_items),
                        "url": str(response.url)
                    })
                    
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
                    
                    # Extract total count from first page (informational only)
                    if page_index == 1:
                        total_count = self._extract_total_count(soup, len(page_items))
                        logger.info(f"Estimated total count: {total_count}")
                    
                    # Check if we should continue to next page
                    if not crawl_all:
                        # For polling, only check first page
                        break
                    
                    # For crawl_all, use proper next-page detection
                    if not self._has_next_page(soup, startat):
                        logger.info(f"No next page detected after page {page_index} (startat={startat})")
                        break
                    
                    # Adaptive delay - faster for large crawls, polite for small ones
                    if crawl_all and page_index > 10:
                        await asyncio.sleep(0.2)  # Faster delay for large crawls
                    else:
                        await asyncio.sleep(0.4)  # Polite delay for small crawls
                    page_index += 1
                    
                except Exception as e:
                    logger.error(f"Error fetching page {page_index}: {e}")
                    break
        
        # Enrich with posted_ts for unseen items (if needed)
        # This is handled by SearchService for militaria321 items
        
        # Log summary for large crawls
        if crawl_all and pages_scanned > 1:
            logger.info({
                "event": "m321_crawl_complete",
                "q": keyword,
                "total_items": len(all_items),
                "pages_scanned": pages_scanned,
                "unique_ids": len(seen_ids),
                "crawl_mode": "full" if crawl_all else "polling"
            })
        
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
                
                # Extract canonical numeric ID from URL (multiple patterns)
                platform_id = self._extract_canonical_id(relative_url, container)
                if not platform_id:
                    continue
                
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
    
    def _extract_canonical_id(self, url: str, container) -> Optional[str]:
        """Extract canonical numeric ID from militaria321 URL or container"""
        # Try multiple patterns to extract numeric ID
        patterns = [
            r'auktion/(\d+)',           # /auktion/12345/...
            r'auktion=(\d+)',           # ?auktion=12345
            r'id=(\d+)',                # ?id=12345
            r'(?:^|/)(\d{6,})',         # Any 6+ digit number in URL
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # Try to find "Auktions-Nr." in container text
        if container:
            text = container.get_text()
            auktion_nr_match = re.search(r'Auktions?-Nr\.?\s*:?\s*(\d+)', text, re.IGNORECASE)
            if auktion_nr_match:
                return auktion_nr_match.group(1)
        
        # Fallback: extract any number from URL path
        url_parts = url.split('/')
        for part in url_parts:
            if part.isdigit() and len(part) >= 4:  # At least 4 digits
                return part
        
        return None
    
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
    
    def _has_next_page(self, soup: BeautifulSoup, current_startat: int) -> bool:
        """Detect if there's a next page by scanning for startat values > current"""
        try:
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                match = re.search(r"startat=(\d+)", href)
                if match and int(match.group(1)) > current_startat:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Error detecting next page: {e}")
            return False
    
    def _extract_total_count(self, soup: BeautifulSoup, items_on_page: int) -> Optional[int]:
        """Extract total result count from search page"""
        try:
            # Look for result count indicators
            text = soup.get_text()
            
            # Enhanced patterns for German result count
            patterns = [
                r'(\d+)\s+Treffer',           # "X Treffer"
                r'(\d+)\s+Auktionen',         # "X Auktionen"  
                r'Gesamt\s*:\s*(\d+)',        # "Gesamt: X"
                r'Ergebnisse\s*:\s*(\d+)',    # "Ergebnisse: X"
                r'(\d+)\s+(?:Ergebnis|gefunden)',  # "X Ergebnis gefunden"
                r'(\d+)\s+(?:von|aus|total)',      # "X von Y"
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    count = int(match.group(1))
                    logger.debug(f"Extracted total count: {count} using pattern: {pattern}")
                    return count
            
            # No count found - return None to let pagination handle it
            logger.debug("No total count found, will rely on next-page detection")
            return None
            
        except Exception as e:
            logger.warning(f"Error extracting total count: {e}")
            return None
    
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
        """Fetch posted_ts and complete missing prices from detail pages
        
        Only fetches for items that don't already have posted_ts or missing price
        """
        items_to_fetch = [
            item for item in items 
            if item.posted_ts is None or item.price_value is None
        ]
        
        if not items_to_fetch:
            return
        
        semaphore = asyncio.Semaphore(min(concurrency, 5))  # Max 5 concurrent requests
        logger.info(f"Fetching detail pages for {len(items_to_fetch)} items (concurrency={min(concurrency, 5)})")
        
        async def fetch_one(item: Listing):
            async with semaphore:
                try:
                    # Add small jitter to avoid overwhelming server
                    await asyncio.sleep(0.2 + (0.3 * asyncio.get_event_loop().time() % 1))
                    
                    detail_data = await self._fetch_detail_page_data(item.url)
                    
                    if detail_data.get('posted_ts'):
                        item.posted_ts = detail_data['posted_ts']
                    
                    if detail_data.get('price_value') and item.price_value is None:
                        item.price_value = detail_data['price_value']
                        item.price_currency = detail_data.get('price_currency', 'EUR')
                    
                except Exception as e:
                    logger.warning(f"Failed to fetch detail data for {item.platform_id}: {e}")
        
        # Process in batches
        tasks = [fetch_one(item) for item in items_to_fetch]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info(f"Detail page enrichment completed for {len(items_to_fetch)} items")
    
    async def _fetch_detail_page_data(self, url: str) -> dict:
        """Fetch posted timestamp and price from item detail page"""
        async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                
                if "iso-8859-1" in response.headers.get("content-type", "").lower():
                    response.encoding = "utf-8"
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                result = {}
                
                # Extract posted timestamp
                posted_ts = self._parse_posted_ts_from_html(soup)
                if posted_ts:
                    result['posted_ts'] = posted_ts
                
                # Extract price if missing
                price_value, price_currency = self._parse_price_from_detail_page(soup)
                if price_value:
                    result['price_value'] = price_value
                    result['price_currency'] = price_currency
                
                return result
                
            except Exception as e:
                logger.warning(f"Error fetching detail page {url}: {e}")
                return {}
    
    def _parse_posted_ts_from_html(self, soup: BeautifulSoup) -> Optional[datetime]:
        """Parse posted timestamp from German HTML with comprehensive patterns"""
        try:
            text = soup.get_text()
            
            # Comprehensive German date patterns for Auktionsstart/Eingestellt
            pattern = (
                r'(?i)(Auktionsstart|Auktionsbeginn|Eingestellt|Angebotsbeginn|Start)\s*:?\s*'
                r'(\d{1,2}\.\d{1,2}\.\d{4})\s*(?:um\s*)?(\d{1,2}:\d{2})\s*Uhr'
            )
            
            match = re.search(pattern, text)
            if match:
                label = match.group(1)  # "Auktionsstart"
                date_str = match.group(2)  # "04.10.2025"
                time_str = match.group(3)  # "13:21"
                
                # Parse German date format
                dt_str = f"{date_str} {time_str}"
                dt_berlin = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
                
                # Convert from Berlin timezone to UTC
                dt_berlin = dt_berlin.replace(tzinfo=self.berlin_tz)
                dt_utc = dt_berlin.astimezone(timezone.utc).replace(tzinfo=None)
                
                logger.debug(f"Parsed {label}: {date_str} {time_str} -> {dt_utc} UTC")
                return dt_utc
            
            # Fallback: simple date pattern without label
            fallback_pattern = r'(\d{1,2}\.\d{1,2}\.\d{4})\s*(?:um\s*)?(\d{1,2}:\d{2})\s*Uhr'
            match = re.search(fallback_pattern, text)
            if match:
                date_str = match.group(1)
                time_str = match.group(2)
                
                dt_str = f"{date_str} {time_str}"
                dt_berlin = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
                dt_berlin = dt_berlin.replace(tzinfo=self.berlin_tz)
                dt_utc = dt_berlin.astimezone(timezone.utc).replace(tzinfo=None)
                
                return dt_utc
            
        except Exception as e:
            logger.warning(f"Error parsing posted_ts: {e}")
        
        return None
    
    def _parse_price_from_detail_page(self, soup: BeautifulSoup) -> tuple[Optional[float], Optional[str]]:
        """Extract price from detail page if missing from search results"""
        try:
            text = soup.get_text()
            
            # Look for Startpreis/Sofort-Kauf patterns
            price_patterns = [
                r'(?i)Startpreis\s*:?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)\s*€',
                r'(?i)Sofort-?Kauf\s*:?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)\s*€',
                r'(?i)Preis\s*:?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)\s*€',
                r'([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)\s*€',  # Generic EUR pattern
            ]
            
            for pattern in price_patterns:
                match = re.search(pattern, text)
                if match:
                    price_str = match.group(1)
                    # Convert German format to Decimal
                    normalized = price_str.replace('.', '').replace(',', '.')
                    try:
                        price_value = float(Decimal(normalized))
                        return price_value, "EUR"
                    except (InvalidOperation, ValueError):
                        continue
            
        except Exception as e:
            logger.warning(f"Error parsing price from detail page: {e}")
        
        return None, None
