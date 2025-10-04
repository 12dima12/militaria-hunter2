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


class Militaria321Provider(BaseProvider):
    """Provider for militaria321.com"""
    
    def __init__(self):
        super().__init__("militaria321.com")
        self.base_url = "https://www.militaria321.com"
        self.search_url = f"{self.base_url}/search.cfm"
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for matching using Unicode NFKC + casefold + trim"""
        if not text:
            return ""
        return unicodedata.normalize("NFKC", text).casefold().strip()
    
    def matches_keyword(self, title: str, keyword: str) -> bool:
        """Check if title matches keyword using proper tokenization"""
        title_normalized = self._normalize_text(title)
        tokens = [t for t in self._normalize_text(keyword).split() if t]
        
        if not tokens:
            return False
        
        # All tokens must appear as whole words in the title
        return all(re.search(rf"\b{re.escape(token)}\b", title_normalized) for token in tokens)
    
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
            
            # Filter by timestamp if provided
            if since_ts and all_listings:
                # Since militaria321 doesn't provide timestamps, we can't filter reliably
                # In production, you'd need to track listings in database and compare
                pass
            
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
                has_more=has_more or len(unique_listings) >= 20  # Assume more if we got many results
            )
            
        except Exception as e:
            logger.error(f"Error searching militaria321 for '{keyword}': {e}")
            return SearchResult(items=[], total_count=0, has_more=False)
    
    async def _fetch_page(self, query: str, page: int = 1) -> tuple[List[Listing], Optional[int], bool]:
        """Fetch a single page of listings"""
        listings = []
        total_count = None
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
        
        try:
            # Build search URL with query and page - try different parameter formats
            search_params = f"?wort={quote_plus(query)}&act=auctions_search"
            if page > 1:
                search_params += f"&page={page}"
            
            url = f"{self.search_url}{search_params}"
            
            async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
                # Try POST request with form data
                form_data = {
                    'wort': query,
                    'act': 'auctions_search'
                }
                if page > 1:
                    form_data['page'] = str(page)
                
                logger.info(f"POST search to militaria321 page {page} with data: {form_data}")
                response = await client.post(self.search_url, data=form_data)
                response.raise_for_status()
                
                # Debug: Log response details
                logger.info(f"Response status: {response.status_code}, Content length: {len(response.content)}")
                logger.info(f"Response encoding: {response.encoding}")
                logger.info(f"Content-Type: {response.headers.get('content-type', 'unknown')}")
                logger.info(f"Content-Encoding: {response.headers.get('content-encoding', 'none')}")
                logger.debug(f"Response headers: {dict(response.headers)}")
                
                # Use response.text which handles encoding automatically
                content = response.text
                logger.info(f"Content preview: {content[:200]}...")
                
                soup = BeautifulSoup(content, 'html.parser')
                
                # Verify the response actually reflects our query
                page_text = soup.get_text().lower()
                query_normalized = self._normalize_text(query)
                
                # Check if the search was actually performed by looking for query reflection
                query_reflected = False
                search_indicators = [
                    f'suchergebnisse "{query.lower()}"',
                    f'suche nach "{query.lower()}"',
                    f'suchbegriff: {query.lower()}',
                    query.lower() in page_text
                ]
                
                for indicator in search_indicators:
                    if indicator in page_text:
                        query_reflected = True
                        break
                
                if not query_reflected and page == 1:
                    logger.warning(f"Query '{query}' not reflected in search results page")
                    # Check for empty search indicators
                    if 'suchergebnisse ""' in page_text or 'keine treffer' in page_text:
                        logger.info("Search returned no results or empty query")
                        return [], 0, False
                
                # Debug: Check for common error messages or empty result indicators
                error_indicators = [
                    "No results found",
                    "0 Items found", 
                    "keine Treffer",
                    "Please check your entry"
                ]
                
                page_text = soup.get_text().lower()
                for indicator in error_indicators:
                    if indicator.lower() in page_text:
                        logger.info(f"Found error indicator on page: '{indicator}'")
                
                # Debug: Log some of the page content to see what we're getting
                if query.lower() == "kappmesser" and page == 1:
                    logger.info(f"Debug - Page title: {soup.title.string if soup.title else 'No title'}")
                    
                    # Look for any links that might be auction items
                    all_links = soup.find_all('a', href=True)
                    auction_related_links = []
                    for link in all_links:
                        href = link.get('href', '')
                        if any(pattern in href.lower() for pattern in ['auktion', 'detail', 'item', 'lot']):
                            auction_related_links.append((href, link.get_text().strip()[:50]))
                    
                    logger.info(f"Debug - Found {len(auction_related_links)} auction-related links")
                    for i, (href, text) in enumerate(auction_related_links[:5]):
                        logger.info(f"Debug - Auction link {i+1}: {href} - {text}")
                    
                    # Look for form elements and search results indicators
                    forms = soup.find_all('form')
                    logger.info(f"Debug - Found {len(forms)} forms")
                    
                    # Look for any text indicating results count
                    text_content = soup.get_text()
                    result_patterns = ['treffer', 'result', 'gefunden', 'found', 'artikel', 'auktion']
                    for pattern in result_patterns:
                        if pattern in text_content.lower():
                            # Find lines containing the pattern
                            lines = [line.strip() for line in text_content.split('\n') if pattern in line.lower() and line.strip()]
                            if lines:
                                logger.info(f"Debug - Lines with '{pattern}': {lines[:3]}")
                    
                    # Check if there's a message about no results
                    no_result_patterns = ['keine treffer', 'no results', '0 gefunden', 'nothing found']
                    for pattern in no_result_patterns:
                        if pattern in text_content.lower():
                            logger.info(f"Debug - Found no-results pattern: '{pattern}'")
                
                listings, total_count, has_more = self._parse_search_page(soup, query, page)
                
                logger.info(f"Page {page}: Found {len(listings)} listings")
                return listings, total_count, has_more
                
        except httpx.RequestError as e:
            logger.error(f"Request error fetching militaria321 page {page}: {e}")
            return [], None, False
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching militaria321 page {page}: {e.response.status_code}")
            return [], None, False
        except Exception as e:
            logger.error(f"Unexpected error fetching militaria321 page {page}: {e}")
            return [], None, False
    
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
            
            # Look for actual auction items, not navigation
            # First try to find auction detail links specifically
            auction_links = soup.find_all('a', href=lambda x: x and 'auktionsdetails' in str(x))
            
            if auction_links:
                listing_elements = []
                # For each auction link, try to find its parent container
                for link in auction_links:
                    # Find the parent row or container
                    parent = link.find_parent('tr') or link.find_parent('td') or link.find_parent('div')
                    if parent and parent not in listing_elements:
                        listing_elements.append(parent)
                logger.info(f"Found {len(auction_links)} auction detail links, {len(listing_elements)} unique containers")
            else:
                # Fallback to other selectors excluding navigation
                listing_selectors = [
                    'tr[bgcolor]',  # Table rows with background color
                    'tr[onclick]',   # Clickable table rows
                    'td[class*="item"]',
                    'div[class*="auction"]',
                    'tr:has(a[href*="auktion"])',  # Rows containing auction links
                ]
                
                listing_elements = []
                for selector in listing_selectors:
                    elements = soup.select(selector)
                    if elements:
                        # Filter out navigation elements
                        filtered = []
                        for elem in elements:
                            text = elem.get_text().lower()
                            # Skip navigation items
                            if any(nav_word in text for nav_word in ['startseite', 'suchen', 'browse', 'shops', 'login', 'hilfe']):
                                continue
                            # Skip very short content (likely navigation)
                            if len(text.strip()) < 20:
                                continue
                            filtered.append(elem)
                        
                        if filtered:
                            listing_elements = filtered
                            logger.info(f"Using selector '{selector}' - found {len(filtered)} non-nav elements")
                            break
            
            # listing_elements is now set above
            
            # Parse individual listings with keyword matching
            for element in listing_elements[:50]:  # Limit to 50 results per page
                try:
                    listing = self._parse_single_listing(element, original_query)
                    if listing and listing.platform_id:
                        # Apply strict keyword matching on title only
                        if self.matches_keyword(listing.title, original_query):
                            listings.append(listing)
                        else:
                            logger.debug(f"Filtered out non-matching item: '{listing.title}' for query '{original_query}'")
                except Exception as e:
                    logger.debug(f"Error parsing single listing: {e}")
                    continue
            
            # No fabricated data - return empty if no real results found
            if not listings:
                logger.info(f"No real listings found for '{original_query}'")
                
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
        """Parse a single listing element"""
        try:
            # Extract title
            title_selectors = ['h1', 'h2', 'h3', '.title', '.name', '.product-title', 'a']
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
                last_seen_ts=datetime.utcnow()
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
            logger.debug(f"Price parsing: '{price_text}' -> {decimal_value} {currency}")
            
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
        return str(abs(hash(url)))
    
    def build_query(self, keyword: str) -> str:
        """Build militaria321-specific search query"""
        # Simple query normalization
        return keyword.strip()