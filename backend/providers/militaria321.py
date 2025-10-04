import httpx
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional, Tuple
import logging
import asyncio
from urllib.parse import urljoin, quote_plus
import re
import unicodedata
from decimal import Decimal, InvalidOperation
import random
import pytz

from .base import BaseProvider
from models import Listing, SearchResult

logger = logging.getLogger(__name__)


class Militaria321Provider(BaseProvider):
    """Provider for militaria321.com"""
    
    def __init__(self):
        super().__init__("militaria321.com")
        self.base_url = "https://www.militaria321.com"
        self.search_url = f"{self.base_url}/search.cfm"
        self._tz_berlin = pytz.timezone("Europe/Berlin")
    
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
        
        # Check each token individually with context awareness
        for token in tokens:
            # Find all occurrences of the token
            pattern = rf"(?<!\w){re.escape(token)}(?!\w)"
            matches = list(re.finditer(pattern, title_normalized, re.UNICODE))
            
            if not matches:
                return False  # Token not found as whole word
            
            # For certain tokens, apply context filtering to avoid false positives
            if token == 'uhr':
                # Check if any match is NOT in a timestamp context
                valid_match_found = False
                for match in matches:
                    start, end = match.span()
                    
                    # Get context around the match (20 chars before and after)
                    context = title_normalized[max(0, start-20):end+20]
                    
                    # Skip if it looks like a timestamp (e.g., "07:39 uhr", "12:30 uhr")
                    if re.search(r'\d{1,2}:\d{2}\s*uhr', context):
                        continue
                    
                    # Skip if it follows common time expressions
                    if re.search(r'(time|zeit|ende|end|bis|um)\s*:?\s*\d.*uhr', context):
                        continue
                    
                    # If we get here, it's likely a valid match (not a timestamp)
                    valid_match_found = True
                    break
                
                if not valid_match_found:
                    return False
        
        return True
    
    def parse_price(self, raw_price: str) -> Tuple[Optional[Decimal], str]:
        """Parse price string and return (decimal_value, currency_code)"""
        if not raw_price or not raw_price.strip():
            return None, "EUR"
        
        text = raw_price.strip()
        
        # Find currency
        currency = "EUR"  # Default
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
        
        # Convert to string with 2 decimal places
        value_str = f"{value:.2f}"
        
        # Split into integer and decimal parts
        parts = value_str.split('.')
        integer_part = parts[0]
        decimal_part = parts[1]
        
        # Add thousands separators (dots) every 3 digits from right
        if len(integer_part) > 3:
            # Reverse, add dots every 3 chars, reverse back
            reversed_int = integer_part[::-1]
            with_dots = '.'.join(reversed_int[i:i+3] for i in range(0, len(reversed_int), 3))
            integer_part = with_dots[::-1]
        
        # Format: "1.234,56 €" (dot for thousands, comma for decimal, space before currency)
        currency_symbol = "€" if currency == "EUR" else currency
        return f"{integer_part},{decimal_part} {currency_symbol}"
        
    async def search(self, keyword: str, since_ts: Optional[datetime] = None, sample_mode: bool = False) -> SearchResult:
        """Search militaria321.com for listings"""
        try:
            query = self.build_query(keyword)
            
            # In sample_mode, fetch more pages for better total_count estimation
            max_pages = 3 if sample_mode else 1
            
            all_listings = []
            total_estimated = 0
            has_more = False
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'same-origin',
                'Cache-Control': 'no-cache',
            }
            
            async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
                for page in range(1, max_pages + 1):
                    page_listings, page_total, page_has_more, soup, page_url = await self._fetch_page(client, query, page)
                    
                    if page_listings:
                        all_listings.extend(page_listings)
                        
                        # Update total estimate from first page that returns results
                        if page_total and total_estimated == 0:
                            total_estimated = page_total
                        
                        # If this page has more, overall result has more
                        if page_has_more:
                            has_more = True
                    else:
                        # No results on this page, stop pagination
                        break
                    
                    # Small delay between pages to be respectful
                    if page &lt; max_pages:
                        await asyncio.sleep(1)
            
            # Deduplicate listings by platform_id
            seen_ids = set()
            unique_listings = []
            for listing in all_listings:
                if listing.platform_id not in seen_ids:
                    seen_ids.add(listing.platform_id)
                    unique_listings.append(listing)
            
            return SearchResult(
                items=unique_listings,
                total_count=total_estimated if total_estimated &gt; 0 else None,
                has_more=has_more or len(unique_listings) &gt;= 20  # Assume more if we got many results
            )
            
        except Exception as e:
            logger.error(f"Error searching militaria321 for '{keyword}': {e}")
            return SearchResult(items=[], total_count=0, has_more=False)
    
    async def _fetch_page(self, client: httpx.AsyncClient, query: str, page: int = 1) -> tuple[List[Listing], Optional[int], bool, Optional[BeautifulSoup], str]:
        """Fetch a single page of listings"""
        listings = []
        total_count = None
        has_more = False
        
        try:
            # Build correct search parameters - militaria321 uses 'q' parameter
            params = {'q': query}
            if page &gt; 1:
                params['startat'] = ((page - 1) * 50) + 1  # Pagination offset
            
            logger.info(f"GET search to militaria321 page {page} with params: {params}")
            response = await client.get(self.search_url, params=params)
            response.raise_for_status()
            
            content = response.text
            soup = BeautifulSoup(content, 'html.parser')
            
            # Verify the response actually reflects our query
            page_text = soup.get_text().lower()
            query_normalized = self._normalize_text(query).lower()
            
            # Check if the search was actually performed
            query_reflected = query_normalized in page_text
            
            if not query_reflected and page == 1:
                logger.warning(f"Query '{query}' (normalized: '{query_normalized}') not reflected in search results page")
                
                # Check for empty search indicators
                empty_indicators = ['keine treffer', 'keine ergebnisse', 'no results found']
                for indicator in empty_indicators:
                    if indicator in page_text:
                        logger.info(f"Found empty result indicator: '{indicator}'")
                        return [], 0, False, soup, str(response.url)
                
                # If query not reflected and no results, likely failed search
                logger.warning("Query not reflected and no empty result indicators - treating as failed search")
                return [], 0, False, soup, str(response.url)
            
            logger.info(f"Query '{query}' successfully reflected in search results")
            
            # Log auction links found on page for debugging
            auction_links = soup.find_all('a', href=lambda x: x and 'auktion' in str(x).lower())
            logger.info(f"Found {len(auction_links)} auction-related links on page")
            
            listings, total_count, has_more = self._parse_search_page(soup, query, page)
            
            logger.info(f"Page {page}: Found {len(listings)} listings")
            return listings, total_count, has_more, soup, str(response.url)
            
        except httpx.RequestError as e:
            logger.error(f"Request error fetching militaria321 page {page}: {e}")
            return [], None, False, None, self.search_url
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching militaria321 page {page}: {e.response.status_code}")
            return [], None, False, None, self.search_url
        except Exception as e:
            logger.error(f"Unexpected error fetching militaria321 page {page}: {e}")
            return [], None, False, None, self.search_url
    
    def _parse_search_page(self, soup: BeautifulSoup, original_query: str, page: int) -> tuple[List[Listing], Optional[int], bool]:
        """Parse listings from militaria321.com search results page"""
        listings = []
        total_count = None
        has_more = False
        
        try:
            # Try to extract total count from search results text
            total_count = self._extract_total_count(soup)
            
            # Check if there are more pages
            has_more = self._has_next_page(soup)
            
            # Find auction links - militaria321 uses various auction URL patterns
            auction_links = soup.find_all('a', href=lambda x: x and 'auktion' in str(x).lower())
            
            logger.info(f"Found {len(auction_links)} auction links on page")
            
            # Debug: log some hrefs to see the pattern
            if auction_links and len(auction_links) &gt; 0:
                logger.debug(f"Sample auction hrefs: {[a.get('href') for a in auction_links[:3]]}")
            
            if not auction_links:
                # No auction items found
                logger.info(f"No auction links found for query '{original_query}'")
                return [], total_count, has_more
            
            # Build unique listing containers from auction links
            # Use the parent &lt;tr&gt; row as the container since it has all the details
            seen_containers = set()
            listing_containers = []
            
            for link in auction_links:
                # Find parent table row
                container = link.find_parent('tr')
                
                if not container:
                    # If no tr parent, skip this link
                    continue
                
                # Avoid duplicate containers (multiple links might be in same row)
                container_id = id(container)
                if container_id not in seen_containers:
                    seen_containers.add(container_id)
                    listing_containers.append(container)
            
            logger.info(f"Processing {len(listing_containers)} unique listing containers")
            
            # Parse individual listings with keyword matching
            for i, container in enumerate(listing_containers[:50]):  # Limit to 50 results per page
                try:
                    listing = self._parse_single_listing(container, original_query)
                    if not listing:
                        logger.debug(f"Container {i+1}: Failed to parse listing (returned None)")
                        continue
                    
                    if not listing.platform_id:
                        logger.debug(f"Container {i+1}: No platform_id extracted")
                        continue
                    
                    # Apply strict keyword matching on title only
                    if self.matches_keyword(listing.title, original_query):
                        listings.append(listing)
                        logger.info(f"✓ Matched #{i+1}: '{listing.title}' -&gt; ID:{listing.platform_id}")
                    else:
                        logger.debug(f"✗ Container {i+1}: Title '{listing.title}' doesn't match query '{original_query}'")
                except Exception as e:
                    logger.warning(f"Error parsing container {i+1}: {e}", exc_info=True)
                    continue
            
            logger.info(f"Successfully parsed {len(listings)} matching listings for '{original_query}'")
                
        except Exception as e:
            logger.error(f"Error parsing militaria321 search page: {e}")
        
        return listings, total_count, has_more
    
    def _extract_total_count(self, soup: BeautifulSoup) -> Optional[int]:
        """Extract total result count from page"""
        # Look for result count text patterns
        count_selectors = [
            '.result-count',
            '.search-count',
            '.total-results',
            '[class*="count"]',
            '[class*="result"]'
        ]
        
        for selector in count_selectors:
            element = soup.select_one(selector)
            if element:
                text = element.get_text()
                # Look for numbers in the text
                numbers = re.findall(r'\d+', text)
                if numbers:
                    try:
                        return int(numbers[-1])  # Usually the last number is total
                    except ValueError:
                        continue
        
        return None
    
    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        """Check if there are more pages"""
        next_selectors = [
            'a[href*="page="]',
            '.next-page',
            '.pagination a',
            '[class*="next"]',
            '[class*="more"]'
        ]
        
        for selector in next_selectors:
            if soup.select(selector):
                return True
        
        return False
    
    def _create_sample_listings(self, query: str) -> List[Listing]:
        """Create sample listings for testing when no real results found"""
        sample_listings = [
            Listing(
                platform=self.name,
                platform_id=f"sample_001_{hash(query)}",
                title=f"Wehrmacht {query.capitalize()} - Original WW2",
                url=f"{self.base_url}/sample/001",
                price_value=125.50,
                price_currency="EUR",
                location="Deutschland",
                condition="Gebraucht",
                seller_name="Militaria_Sammler",
                first_seen_ts=datetime.utcnow(),
                last_seen_ts=datetime.utcnow()
            ),
            Listing(
                platform=self.name,
                platform_id=f"sample_002_{hash(query)}",
                title=f"Original {query.capitalize()} 1943 - Selten",
                url=f"{self.base_url}/sample/002",
                price_value=89.00,
                price_currency="EUR",
                location="Bayern",
                condition="Sehr gut",
                seller_name="Historica_Shop",
                first_seen_ts=datetime.utcnow(),
                last_seen_ts=datetime.utcnow()
            ),
            Listing(
                platform=self.name,
                platform_id=f"sample_003_{hash(query)}",
                title=f"Deutsche {query.capitalize()} Sammlung - 5 Stück",
                url=f"{self.base_url}/sample/003",
                price_value=245.00,
                price_currency="EUR",
                location="Berlin",
                condition="Gemischt",
                seller_name="Militaria_Berlin",
                first_seen_ts=datetime.utcnow(),
                last_seen_ts=datetime.utcnow()
            ),
            Listing(
                platform=self.name,
                platform_id=f"sample_004_{hash(query)}",
                title=f"Seltene {query.capitalize()} aus Nachlass",
                url=f"{self.base_url}/sample/004",
                price_value=180.75,
                price_currency="EUR",
                location="Hamburg",
                condition="Antik",
                seller_name="Erben_Sammlung",
                first_seen_ts=datetime.utcnow(),
                last_seen_ts=datetime.utcnow()
            ),
            Listing(
                platform=self.name,
                platform_id=f"sample_005_{hash(query)}",
                title=f"{query.capitalize()} Replik - Hohe Qualität",
                url=f"{self.base_url}/sample/005",
                price_value=45.00,
                price_currency="EUR",
                location="Österreich",
                condition="Neu",
                seller_name="Replik_Meister",
                first_seen_ts=datetime.utcnow(),
                last_seen_ts=datetime.utcnow()
            )
        ]
        
        logger.info(f"Created {len(sample_listings)} sample listings for '{query}'")
        return sample_listings
    
    def _parse_single_listing(self, element, original_query: str) -> Optional[Listing]:
        """Parse a single listing element from a table row"""
        try:
            # Find auction link within the row
            auction_link = element.find('a', href=lambda x: x and 'auktion' in str(x).lower())
            
            if not auction_link:
                logger.debug(f"No auction link found in row")
                return None
            
            # Extract title from link text
            title = auction_link.get_text().strip()
            if not title or len(title) &lt; 3:
                logger.debug(f"Title too short or empty: '{title}'")
                return None
            
            # Clean title (limit length)
            title = title[:200]
            logger.debug(f"Extracted title: '{title}'")
            
            # Extract URL from link
            href = auction_link.get('href')
            if not href:
                logger.debug(f"No href in auction link")
                return None
            
            url = urljoin(self.base_url, href)
            logger.debug(f"Extracted URL: '{url}'")
            
            # Extract price using new robust parsing
            price_value, price_currency = self._extract_price_robust(element)
            
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
                last_seen_ts=datetime.utcnow(),
                posted_ts=None,
            )
            
        except Exception as e:
            logger.debug(f"Error parsing single listing element: {e}")
            return None
    
    def _extract_price_robust(self, element):
        """Extract price from listing element using robust parsing"""
        price_selectors = ['.price', '.cost', '.amount', '[class*="price"]', '[id*="price"]']
        price_text = None
        
        for selector in price_selectors:
            price_elem = element.select_one(selector)
            if price_elem:
                price_text = price_elem.get_text().strip()
                break
        
        if not price_text:
            # Look for currency patterns in element text
            text = element.get_text()
            # Look for price patterns: numbers followed by currency
            price_patterns = [
                r'([\d.,]+)\s*([€$£]|EUR|USD|GBP)',
                r'([€$£])\s*([\d.,]+)',
                r'(EUR|USD|GBP)\s*([\d.,]+)',
            ]
            
            for pattern in price_patterns:
                match = re.search(pattern, text)
                if match:
                    price_text = match.group()
                    break
        
        if price_text:
            # Use the robust price parsing
            decimal_value, currency = self.parse_price(price_text)
            logger.debug(f"Price parsing: '{price_text}' -&gt; {decimal_value} {currency}")
            
            # Convert Decimal back to float for storage (maintaining precision)
            float_value = float(decimal_value) if decimal_value else None
            return float_value, currency
        
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
        condition_keywords = ['neu', 'gebraucht', 'new', 'used', 'excellent', 'good', 'fair', 'poor', 'sehr gut', 'gut', 'antik']
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
            r'/auktion/(\d+)',  # militaria321 pattern: /auktion/7580057/...
            r'/item/(\d+)',
            r'/product/(\d+)', 
            r'/listing/(\d+)',
            r'id=(\d+)',
            r'item_id=(\d+)',
            r'/(\d{7,})',  # Any 7+ digit number in URL
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # Fallback: use URL hash
        return str(abs(hash(url)))
    
    def build_query(self, keyword: str) -> str:
        """Build militaria321-specific search query"""
        # Simple query normalization
        return keyword.strip()
    
    def _get_next_page_url(self, current_url: str, soup: BeautifulSoup) -> Optional[str]:
        """Get next page URL for pagination"""
        from providers.pagination_utils import get_next_page_url_militaria321
        return get_next_page_url_militaria321(current_url, soup)

    # -------------------- posted_ts support --------------------
    def _parse_posted_ts_from_text(self, text: str) -> Optional[datetime]:
        """Parse German date like '04.10.2025 13:21 Uhr' from text and return UTC-aware datetime."""
        try:
            m = re.search(r'(?:Auktionsbeginn|Eingestellt)\s*:?[\s\xa0]*?(\d{1,2}\.\d{1,2}\.\d{4})\s+(\d{1,2}:\d{2})\s*Uhr', text, re.IGNORECASE)
            if not m:
                # Try generic dd.mm.yyyy HH:MM Uhr without label
                m = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})\s+(\d{1,2}:\d{2})\s*Uhr', text)
            if not m:
                return None
            date_part = m.group(1)
            time_part = m.group(2)
            dt_naive = datetime.strptime(f"{date_part} {time_part}", "%d.%m.%Y %H:%M")
            dt_local = self._tz_berlin.localize(dt_naive)
            dt_utc = dt_local.astimezone(pytz.utc)
            return dt_utc
        except Exception:
            return None

    def _parse_posted_ts_from_soup(self, soup: BeautifulSoup) -> Optional[datetime]:
        # Scan common containers for the labels
        try:
            # Look in definition lists, tables, and generic text
            # 1) dt/dd pattern
            for dt in soup.find_all(['dt', 'th']):
                label = dt.get_text(strip=True)
                if re.search(r'(Auktionsbeginn|Eingestellt)', label, re.IGNORECASE):
                    # corresponding dd/td
                    sib = dt.find_next('dd') or dt.find_next('td')
                    if sib:
                        ts = self._parse_posted_ts_from_text(sib.get_text(" ", strip=True))
                        if ts:
                            return ts
            # 2) table rows
            for tr in soup.find_all('tr'):
                cells = tr.find_all(['td', 'th'])
                if len(cells) &gt;= 2:
                    label = cells[0].get_text(" ", strip=True)
                    if re.search(r'(Auktionsbeginn|Eingestellt)', label, re.IGNORECASE):
                        ts = self._parse_posted_ts_from_text(cells[1].get_text(" ", strip=True))
                        if ts:
                            return ts
            # 3) fallback: full text search
            full = soup.get_text(" ", strip=True)
            return self._parse_posted_ts_from_text(full)
        except Exception:
            return None

    async def fetch_posted_ts_batch(self, listings: List[Listing], concurrency: int = 4) -> None:
        """Fetch and set posted_ts for each listing (in place). Skips those already having posted_ts."""
        # Filter targets
        targets = [it for it in listings if it.platform == self.name and not getattr(it, 'posted_ts', None)]
        if not targets:
            return
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        sem = asyncio.Semaphore(concurrency)

        async def worker(item: Listing, client: httpx.AsyncClient):
            async with sem:
                try:
                    await asyncio.sleep(random.uniform(0.2, 0.6))
                    resp = await client.get(item.url, timeout=30.0)
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    ts = self._parse_posted_ts_from_soup(soup)
                    item.posted_ts = ts
                    logger.info(f"posted_ts for {item.platform_id}: {ts}")
                except Exception as e:
                    logger.debug(f"Failed to fetch posted_ts for {item.url}: {e}")

        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            await asyncio.gather(*(worker(it, client) for it in targets))