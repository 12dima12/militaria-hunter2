import httpx
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional, Tuple
import logging
import asyncio
from urllib.parse import urljoin
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .base import BaseProvider
from models import Listing, SearchResult

logger = logging.getLogger(__name__)


class EgunProvider(BaseProvider):
    """Provider for egun.de"""
    
    def __init__(self):
        super().__init__("egun.de")
        self.base_url = "https://www.egun.de/market/"
        self.search_url = f"{self.base_url}list_items.php"
        self.index_url = f"{self.base_url}index.php"
        self._tz_berlin = ZoneInfo("Europe/Berlin")
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for matching using Unicode NFKC + casefold + trim"""
        if not text:
            return ""
        return unicodedata.normalize("NFKC", text).casefold().strip()
    
    def matches_keyword(self, title: str, keyword: str) -> bool:
        """Check if title matches keyword using proper tokenization with context filtering"""
        title_normalized = self._normalize_text(title)
        tokens = [t for t in self._normalize_text(keyword).split() if t]
        
        if not tokens:
            return False
        
        # Check each token individually with whole-word matching
        for token in tokens:
            pattern = rf"(?<![a-zA-Z0-9äöüß]){re.escape(token)}(?![a-zA-Z0-9äöüß])"
            matches = list(re.finditer(pattern, title_normalized, re.UNICODE))
            
            if not matches:
                return False  # Token not found as whole word
        
        return True
    
    def parse_price(self, raw_price: str) -> Tuple[Optional[Decimal], str]:
        """Parse price string and return (decimal_value, currency_code)"""
        if not raw_price or not raw_price.strip():
            return None, "EUR"
        
        text = raw_price.strip()
        
        # Find currency
        currency = "EUR"  # Default for egun.de
        currency_patterns = [
            (r'€', 'EUR'),
            (r'\bEUR\b', 'EUR'),
            (r'\$', 'USD'),
            (r'\bUSD\b', 'USD'),
            (r'£', 'GBP'),
            (r'\bGBP\b', 'GBP'),
        ]
        
        for pattern, curr_code in currency_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                currency = curr_code
                break
        
        # Remove currency symbols and letters to get just numbers and separators
        number_text = re.sub(r'[€$£]|EUR|USD|GBP', '', text, flags=re.IGNORECASE).strip()
        
        # Find all digit sequences with separators
        number_match = re.search(r'[\d.,]+', number_text)
        if not number_match:
            return None, currency
        
        number_str = number_match.group()
        
        try:
            # Detect decimal separator (last occurrence of . or , followed by 1-2 digits)
            decimal_match = re.search(r'[.,](\d{1,2})$', number_str)
            
            if decimal_match:
                # Has decimal part
                decimal_sep = number_str[decimal_match.start()]
                decimal_part = decimal_match.group(1)
                
                # Everything before the decimal separator
                integer_part = number_str[:decimal_match.start()]
                
                # Remove thousands separators (the other separator type)
                thousands_sep = ',' if decimal_sep == '.' else '.'
                integer_part = integer_part.replace(thousands_sep, '')
                
                # Combine and normalize to decimal format
                normalized = f"{integer_part}.{decimal_part}"
            else:
                # No decimal part, just remove thousands separators
                normalized = number_str.replace(',', '').replace('.', '')
            
            return Decimal(normalized), currency
            
        except (InvalidOperation, ValueError) as e:
            logger.debug(f"Failed to parse price '{raw_price}': {e}")
            return None, currency
    
    def format_price_de(self, value: Decimal, currency: str = "EUR") -> str:
        """Format price in German locale style"""
        if value is None:
            return ""
        
        value_str = f"{value:.2f}"
        parts = value_str.split('.')
        integer_part = parts[0]
        decimal_part = parts[1]
        
        if len(integer_part) > 3:
            reversed_int = integer_part[::-1]
            with_dots = '.'.join(reversed_int[i:i+3] for i in range(0, len(reversed_int), 3))
            integer_part = with_dots[::-1]
        
        currency_symbol = "€" if currency == "EUR" else currency
        return f"{integer_part},{decimal_part} {currency_symbol}"
    
    async def search(self, keyword: str, since_ts: Optional[datetime] = None, sample_mode: bool = False, crawl_all: bool = False) -> SearchResult:
        """Search egun.de for listings"""
        try:
            query = self.build_query(keyword)
            
            # In sample_mode, fetch a few pages; in crawl_all, iterate until no more pages
            max_pages = 3 if sample_mode and not crawl_all else (1 if not crawl_all else 10_000)
            
            all_listings = []
            total_estimated = 0
            has_more = False
            pages_scanned_local = 0
            
            page = 1
            while page <= max_pages:
                page_listings, page_total, page_has_more = await self._fetch_page(query, page)
                
                if page_listings:
                    all_listings.extend(page_listings)
                    pages_scanned_local += 1
                    
                    if page_total and total_estimated == 0:
                        total_estimated = page_total
                    
                    if page_has_more:
                        has_more = True

                    # Early-stop: only if all items have posted_ts and all are older than since_ts
                    if since_ts is not None:
                        try:
                            has_any_ts = 0
                            all_older = True
                            for it in page_listings:
                                if getattr(it, 'posted_ts', None) is not None and getattr(it, 'posted_ts').tzinfo is not None:
                                    has_any_ts += 1
                                    if it.posted_ts >= since_ts:
                                        all_older = False
                                        break
                            if has_any_ts == len(page_listings) and all_older:
                                logger.info("Early-stop (egun): page has only items older than since_ts")
                                break
                        except Exception:
                            pass
                else:
                    break
                
                if not crawl_all and page < max_pages:
                    await asyncio.sleep(1)
                if crawl_all and not page_has_more:
                    break
                page += 1
            
            seen_ids = set()
            unique_listings = []
            for listing in all_listings:
                if listing.platform_id not in seen_ids:
                    seen_ids.add(listing.platform_id)
                    unique_listings.append(listing)
            
            return SearchResult(
                items=unique_listings,
                total_count=total_estimated if total_estimated > 0 else None,
                has_more=has_more or len(unique_listings) >= 50,
                pages_scanned=pages_scanned_local
            )
            
        except Exception as e:
            logger.error(f"Error searching egun.de for '{keyword}': {e}")
            return SearchResult(items=[], total_count=0, has_more=False, pages_scanned=0)
    
    async def _fetch_page(self, query: str, page: int = 1) -> tuple[List[Listing], Optional[int], bool]:
        """Fetch a single page of listings"""
        listings = []
        total_count = None
        has_more = False
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'br, gzip, deflate',
            'Referer': self.index_url,
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
                params = {
                    'mode': 'qry',
                    'plusdescr': 'off',
                    'wheremode': 'and',
                    'query': query,
                    'quick': '1'
                }
                
                current_start = 0
                if page > 1:
                    current_start = (page - 1) * 50
                    params['start'] = current_start
                
                logger.info(f"GET search to egun.de page {page} with params: query='{query}'")
                response = await client.get(self.search_url, params=params)
                response.raise_for_status()
                
                if response.encoding is None or response.encoding.lower() == 'iso-8859-1':
                    response.encoding = 'utf-8'
                
                content = response.text
                soup = BeautifulSoup(content, 'html.parser')
                
                page_text = soup.get_text().lower()
                query_normalized = self._normalize_text(query).lower()
                
                query_reflected = query_normalized in page_text
                
                if not query_reflected and page == 1:
                    empty_indicators = ['keine treffer', 'keine ergebnisse', 'no results found']
                    for indicator in empty_indicators:
                        if indicator in page_text:
                            return [], 0, False
                
                # Parse items
                listings, total_count, _ = self._parse_search_page(soup, query, page)
                
                # Determine if there's a NEXT page by inspecting start offsets in anchors
                next_starts = []
                for a in soup.find_all('a', href=True):
                    m = re.search(r'start=(\d+)', a['href'])
                    if m:
                        try:
                            off = int(m.group(1))
                            next_starts.append(off)
                        except Exception:
                            pass
                # Determine next page as the smallest offset greater than current
                greater = sorted([off for off in next_starts if off > current_start])
                next_offset = greater[0] if greater else None
                # Treat as more pages ONLY if next offset is exactly +50 (adjacent step)
                has_more = (next_offset == (current_start + 50))
                
                return listings, total_count, has_more
                
        except httpx.RequestError as e:
            logger.error(f"Request error fetching egun.de page {page}: {e}")
            return [], None, False
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching egun.de page {page}: {e.response.status_code}")
            return [], None, False
        except Exception as e:
            logger.error(f"Unexpected error fetching egun.de page {page}: {e}")
            return [], None, False
    
    def _parse_search_page(self, soup: BeautifulSoup, original_query: str, page: int, apply_filter: bool = True) -> tuple[List[Listing], Optional[int], bool]:
        """Parse listings from egun.de search results page"""
        listings = []
        total_count = None
        has_more = False
        
        try:
            total_count = self._extract_total_count(soup)
            has_more = self._has_next_page(soup)
            
            item_links = soup.find_all('a', href=lambda x: x and 'item.php?id=' in str(x))
            
            if not item_links:
                return [], total_count, has_more
            
            seen_containers = set()
            listing_containers = []
            
            for link in item_links:
                container = link.find_parent('tr')
                if not container:
                    continue
                container_id = id(container)
                if container_id not in seen_containers:
                    seen_containers.add(container_id)
                    listing_containers.append(container)
            
            for i, container in enumerate(listing_containers[:100]):
                try:
                    listing = self._parse_single_listing(container, original_query)
                    if listing and listing.platform_id:
                        if not apply_filter or self.matches_keyword(listing.title, original_query):
                            listings.append(listing)
                except Exception as e:
                    logger.warning(f"Error parsing container {i+1}: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"Error parsing egun.de search page: {e}")
        
        return listings, total_count, has_more
    
    def _parse_single_listing(self, element, original_query: str) -> Optional[Listing]:
        """Parse a single listing element from a table row"""
        try:
            item_link = element.find('a', href=lambda x: x and 'item.php?id=' in str(x))
            
            if not item_link:
                return None
            
            title = item_link.get_text().strip()
            if not title or len(title) < 3:
                return None
            title = title[:200]
            
            href = item_link.get('href')
            if not href:
                return None
            
            url = urljoin(self.base_url, href)
            id_match = re.search(r'id=(\d+)', href)
            if not id_match:
                return None
            platform_id = id_match.group(1)
            
            price_value, price_currency = self._extract_price_robust(element)
            image_url = self._extract_image(element)
            location = self._extract_location(element)
            condition = self._extract_condition(element)
            seller_name = self._extract_seller(element)
            
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
                last_seen_ts=datetime.utcnow(),
                posted_ts=None,
            )
            
        except Exception:
            return None
    
    def _extract_price_robust(self, element):
        """Extract price from listing element using robust parsing"""
        price_td = element.find('td', attrs={'align': 'right', 'nowrap': True})
        
        if price_td:
            price_text = price_td.get_text().strip()
            lines = price_text.split('\n')
            for line in lines:
                if line.strip() and ('EUR' in line or '€' in line):
                    decimal_value, currency = self.parse_price(line.strip())
                    float_value = float(decimal_value) if decimal_value else None
                    return float_value, currency
        
        return None, "EUR"
    
    def _extract_image(self, element):
        img = element.find('img', src=True)
        if img:
            src = img.get('src')
            if src and 'aucimg' in src:
                return urljoin(self.base_url, src)
        return None
    
    def _extract_location(self, element):
        return None
    
    def _extract_condition(self, element):
        condition_keywords = ['neu', 'gebraucht', 'new', 'used', 'excellent', 'good', 'fair', 'sehr gut', 'gut']
        text = element.get_text().lower()
        for keyword in condition_keywords:
            if keyword in text:
                return keyword.capitalize()
        return None
    
    def _extract_seller(self, element):
        return None
    
    def _extract_total_count(self, soup: BeautifulSoup) -> Optional[int]:
        text = soup.get_text()
        count_patterns = [
            r'(\d+)\s+Treffer',
            r'(\d+)\s+Ergebnisse',
            r'(\d+)\s+Artikel',
        ]
        
        for pattern in count_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return None
    
    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        next_selectors = [
            'a[href*="start="]',
            'a:contains("weiter")',
            'a:contains("nächste")',
            'a:contains("next")',
        ]
        for selector in next_selectors:
            if soup.select(selector):
                return True
        return False
    
    def build_query(self, keyword: str) -> str:
        return keyword.strip()
    
    def _get_next_page_url(self, current_url: str, soup: BeautifulSoup) -> Optional[str]:
        from providers.pagination_utils import get_next_page_url_egun
        return get_next_page_url_egun(current_url, soup)
    
    # -------------------- posted_ts support --------------------
    def _parse_posted_ts_from_text(self, text: str) -> Optional[datetime]:
        try:
            # Broad set of labels observed on detail pages
            m = re.search(r'(?:Auktionsbeginn|Eingestellt|Angebotsbeginn|Start|Erstellt|Angelegt)\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{4})\s+(\d{1,2}:\d{2})\s*Uhr', text, re.IGNORECASE)
            if not m:
                m = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})\s+(\d{1,2}:\d{2})\s*Uhr', text)
            if not m:
                return None
            date_part = m.group(1)
            time_part = m.group(2)
            dt_naive = datetime.strptime(f"{date_part} {time_part}", "%d.%m.%Y %H:%M")
            dt_local = dt_naive.replace(tzinfo=self._tz_berlin)
            dt_utc = dt_local.astimezone(ZoneInfo("UTC"))
            return dt_utc
        except Exception:
            return None
    
    def _parse_posted_ts_from_soup(self, soup: BeautifulSoup) -> Optional[datetime]:
        try:
            for dt in soup.find_all(['dt', 'th']):
                label = dt.get_text(strip=True)
                if re.search(r'(Auktionsbeginn|Eingestellt|Angebotsbeginn|Start|Erstellt|Angelegt)', label, re.IGNORECASE):
                    sib = dt.find_next('dd') or dt.find_next('td')
                    if sib:
                        ts = self._parse_posted_ts_from_text(sib.get_text(" ", strip=True))
                        if ts:
                            return ts
            for tr in soup.find_all('tr'):
                cells = tr.find_all(['td', 'th'])
                if len(cells) >= 2:
                    label = cells[0].get_text(" ", strip=True)
                    if re.search(r'(Auktionsbeginn|Eingestellt|Angebotsbeginn|Start|Erstellt|Angelegt)', label, re.IGNORECASE):
                        ts = self._parse_posted_ts_from_text(cells[1].get_text(" ", strip=True))
                        if ts:
                            return ts
            full = soup.get_text(" ", strip=True)
            return self._parse_posted_ts_from_text(full)
        except Exception:
            return None
    
    async def fetch_posted_ts_batch(self, listings: List[Listing], concurrency: int = 4) -> None:
        targets = [it for it in listings if it.platform == self.name and not getattr(it, 'posted_ts', None)]
        if not targets:
            return
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        sem = asyncio.Semaphore(concurrency)
        async def worker(item: Listing, client: httpx.AsyncClient):
            async with sem:
                try:
                    await asyncio.sleep(0.2)
                    resp = await client.get(item.url, timeout=30.0)
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    ts = self._parse_posted_ts_from_soup(soup)
                    item.posted_ts = ts
                    logger.info(f"egun posted_ts for {item.platform_id}: {ts}")
                except Exception as e:
                    logger.debug(f"Failed to fetch egun posted_ts for {item.url}: {e}")
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            await asyncio.gather(*(worker(it, client) for it in targets))