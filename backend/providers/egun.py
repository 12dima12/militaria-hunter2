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
            # Find all occurrences of the token as whole word
            # Use word boundaries, but also treat hyphen/underscore/slash as separators
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
        """Search egun.de for listings"""
        try:
            query = self.build_query(keyword)
            
            # In sample_mode, fetch more pages for better total_count estimation
            max_pages = 3 if sample_mode else 1
            
            all_listings = []
            total_estimated = 0
            has_more = False
            
            for page in range(1, max_pages + 1):
                page_listings, page_total, page_has_more = await self._fetch_page(query, page)
                
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
                if page < max_pages:
                    await asyncio.sleep(1)
            
            # Filter by timestamp if provided (egun doesn't provide timestamps, so we can't filter reliably)
            # In production, track listings in database and compare
            
            # Deduplicate listings by platform_id
            seen_ids = set()
            unique_listings = []
            for listing in all_listings:
                if listing.platform_id not in seen_ids:
                    seen_ids.add(listing.platform_id)
                    unique_listings.append(listing)
            
            return SearchResult(
                items=unique_listings,
                total_count=total_estimated if total_estimated > 0 else None,
                has_more=has_more or len(unique_listings) >= 50  # Assume more if we got many results
            )
            
        except Exception as e:
            logger.error(f"Error searching egun.de for '{keyword}': {e}")
            return SearchResult(items=[], total_count=0, has_more=False)
    
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
                # Build search parameters using discovered form structure
                params = {
                    'mode': 'qry',
                    'plusdescr': 'off',
                    'wheremode': 'and',
                    'query': query,
                    'quick': '1'
                }
                
                # Add pagination if not first page
                if page > 1:
                    params['start'] = (page - 1) * 50  # Assuming 50 items per page
                
                logger.info(f"GET search to egun.de page {page} with params: query='{query}'")
                response = await client.get(self.search_url, params=params)
                response.raise_for_status()
                
                # Debug: Log response details
                logger.info(f"Response status: {response.status_code}, Content length: {len(response.content)}")
                logger.info(f"Response encoding: {response.encoding}")
                logger.info(f"Content-Type: {response.headers.get('content-type', 'unknown')}")
                
                # Set encoding explicitly (egun uses ISO-8859-1 but content is UTF-8)
                if response.encoding is None or response.encoding.lower() == 'iso-8859-1':
                    # Try UTF-8 first for better umlaut support
                    response.encoding = 'utf-8'
                
                content = response.text
                logger.info(f"Content preview: {content[:200]}...")
                
                soup = BeautifulSoup(content, 'html.parser')
                
                # Verify the response actually reflects our query
                page_text = soup.get_text().lower()
                query_normalized = self._normalize_text(query).lower()
                
                # Check if the search was actually performed
                query_reflected = query_normalized in page_text
                
                if not query_reflected and page == 1:
                    logger.warning(f"Query '{query}' (normalized: '{query_normalized}') not reflected in search results page")
                    
                    # Check for empty result indicators
                    empty_indicators = ['keine treffer', 'keine ergebnisse', 'no results found']
                    for indicator in empty_indicators:
                        if indicator in page_text:
                            logger.info(f"Found empty result indicator: '{indicator}'")
                            return [], 0, False
                    
                    # If query not reflected, might still have results
                    logger.info("Query not strongly reflected but continuing to parse...")
                else:
                    logger.info(f"Query '{query}' successfully reflected in search results")
                
                # Log item links found
                item_links = soup.find_all('a', href=lambda x: x and 'item.php' in str(x))
                logger.info(f"Found {len(item_links)} item links on page")
                
                listings, total_count, has_more = self._parse_search_page(soup, query, page)
                
                logger.info(f"Page {page}: Found {len(listings)} listings")
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
    
    def _parse_search_page(self, soup: BeautifulSoup, original_query: str, page: int) -> tuple[List[Listing], Optional[int], bool]:
        """Parse listings from egun.de search results page"""
        listings = []
        total_count = None
        has_more = False
        
        try:
            # Try to extract total count from search results text
            total_count = self._extract_total_count(soup)
            
            # Check if there are more pages
            has_more = self._has_next_page(soup)
            
            # Find item links - egun uses item.php?id=XXXXX pattern
            item_links = soup.find_all('a', href=lambda x: x and 'item.php?id=' in str(x))
            
            logger.info(f"Found {len(item_links)} item links on page")
            
            if not item_links:
                # No items found
                logger.info(f"No item links found for query '{original_query}'")
                return [], total_count, has_more
            
            # Build unique listing containers from item links
            # Use the parent <tr> row as the container since it has all the details
            seen_containers = set()
            listing_containers = []
            
            for link in item_links:
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
            for i, container in enumerate(listing_containers[:100]):  # Cap at 100 per page
                try:
                    listing = self._parse_single_listing(container, original_query)
                    if listing and listing.platform_id:
                        # Apply strict keyword matching on title only
                        if self.matches_keyword(listing.title, original_query):
                            listings.append(listing)
                            logger.debug(f"✓ Matched #{i+1}: '{listing.title}' -> ID:{listing.platform_id}")
                        else:
                            logger.debug(f"✗ Container {i+1}: Title '{listing.title}' doesn't match query '{original_query}'")
                except Exception as e:
                    logger.warning(f"Error parsing container {i+1}: {e}")
                    continue
            
            logger.info(f"Successfully parsed {len(listings)} matching listings for '{original_query}'")
                
        except Exception as e:
            logger.error(f"Error parsing egun.de search page: {e}")
        
        return listings, total_count, has_more
    
    def _parse_single_listing(self, element, original_query: str) -> Optional[Listing]:
        """Parse a single listing element from a table row"""
        try:
            # Find item link within the row
            item_link = element.find('a', href=lambda x: x and 'item.php?id=' in str(x))
            
            if not item_link:
                logger.debug(f"No item link found in row")
                return None
            
            # Extract title from link text
            title = item_link.get_text().strip()
            if not title or len(title) < 3:
                logger.debug(f"Title too short or empty: '{title}'")
                return None
            
            # Clean title (limit length)
            title = title[:200]
            logger.debug(f"Extracted title: '{title}'")
            
            # Extract URL and ID from link
            href = item_link.get('href')
            if not href:
                logger.debug(f"No href in item link")
                return None
            
            url = urljoin(self.base_url, href)
            
            # Extract platform_id from URL (item.php?id=XXXXX)
            id_match = re.search(r'id=(\d+)', href)
            if not id_match:
                logger.debug(f"Could not extract ID from URL: {href}")
                return None
            
            platform_id = id_match.group(1)
            logger.debug(f"Extracted URL: '{url}', ID: {platform_id}")
            
            # Extract price using robust parsing
            price_value, price_currency = self._extract_price_robust(element)
            
            # Extract image
            image_url = self._extract_image(element)
            
            # Extract other details
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
                last_seen_ts=datetime.utcnow()
            )
            
        except Exception as e:
            logger.debug(f"Error parsing single listing element: {e}")
            return None
    
    def _extract_price_robust(self, element):
        """Extract price from listing element using robust parsing"""
        # egun shows prices in <td align="right" nowrap> format like "599,00 EUR"
        price_td = element.find('td', attrs={'align': 'right', 'nowrap': True})
        
        if price_td:
            price_text = price_td.get_text().strip()
            # Sometimes there are multiple prices (original + strikethrough)
            # Take the first non-italic one (current price)
            lines = price_text.split('\n')
            for line in lines:
                if line.strip() and 'EUR' in line or '€' in line:
                    # Use the robust price parsing
                    decimal_value, currency = self.parse_price(line.strip())
                    logger.debug(f"Price parsing: '{line.strip()}' -> {decimal_value} {currency}")
                    
                    # Convert Decimal to float for storage
                    float_value = float(decimal_value) if decimal_value else None
                    return float_value, currency
        
        return None, "EUR"
    
    def _extract_image(self, element):
        """Extract image URL from listing element"""
        img = element.find('img', src=True)
        if img:
            src = img.get('src')
            if src and 'aucimg' in src:  # egun uses aucimg for auction images
                return urljoin(self.base_url, src)
        return None
    
    def _extract_location(self, element):
        """Extract location from listing element"""
        # egun doesn't always show location in list view
        return None
    
    def _extract_condition(self, element):
        """Extract condition from listing element"""
        condition_keywords = ['neu', 'gebraucht', 'new', 'used', 'excellent', 'good', 'fair', 'sehr gut', 'gut']
        text = element.get_text().lower()
        for keyword in condition_keywords:
            if keyword in text:
                return keyword.capitalize()
        return None
    
    def _extract_seller(self, element):
        """Extract seller name from listing element"""
        # egun doesn't show seller in list view typically
        return None
    
    def _extract_total_count(self, soup: BeautifulSoup) -> Optional[int]:
        """Extract total result count from page"""
        # Look for result count text patterns like "X Treffer"
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
        """Check if there are more pages"""
        # Look for pagination links
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
        """Build egun-specific search query"""
        # Simple query normalization (preserve original for better matching)
        return keyword.strip()
    
    def _get_next_page_url(self, current_url: str, soup: BeautifulSoup) -> Optional[str]:
        """Get next page URL for pagination"""
        from providers.pagination_utils import get_next_page_url_egun
        return get_next_page_url_egun(current_url, soup)
