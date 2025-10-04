import httpx
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
import logging
import asyncio
from urllib.parse import urljoin, quote_plus
import re

from .base import BaseProvider
from models import Listing, SearchResult

logger = logging.getLogger(__name__)


class Militaria321Provider(BaseProvider):
    """Provider for militaria321.com"""
    
    def __init__(self):
        super().__init__("militaria321.com")
        self.base_url = "https://www.militaria321.com"
        self.search_url = f"{self.base_url}/search.cfm"
        
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
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Cache-Control': 'no-cache',
        }
        
        try:
            # Build search URL with query and page
            search_params = f"?wort={quote_plus(query)}"
            if page > 1:
                search_params += f"&page={page}"
            
            url = f"{self.search_url}{search_params}"
            
            async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
                logger.info(f"Fetching militaria321 page {page}: {url}")
                response = await client.get(url)
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
                    logger.info(f"Debug - Page text snippet: {page_text[:500]}")
                    
                    # Look for specific militaria321 patterns
                    auction_links = soup.find_all('a', href=lambda x: x and 'auktionsdetails' in x)
                    logger.info(f"Debug - Found {len(auction_links)} auction detail links")
                    
                    if auction_links:
                        for i, link in enumerate(auction_links[:3]):
                            logger.info(f"Debug - Link {i+1}: {link.get('href')} - {link.get_text().strip()[:50]}")
                    
                    # Look for table structures
                    tables = soup.find_all('table')
                    logger.info(f"Debug - Found {len(tables)} tables")
                    
                    # Look for any tr elements with content
                    rows = soup.find_all('tr')
                    content_rows = [row for row in rows if row.get_text().strip() and len(row.get_text().strip()) > 20]
                    logger.info(f"Debug - Found {len(content_rows)} content rows")
                
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
            
            # Look for listing elements with various selectors (militaria321 specific)
            listing_selectors = [
                'tr[bgcolor]',  # Table rows with background color (common pattern)
                'table tr',     # Table-based layout
                '.auction',
                '.auktion', 
                '.listing',
                '.item',
                'td.itemtitle',
                'a[href*="auktionsdetails"]',  # Links to auction details
                '[class*="auction"]',
                '[id*="auction"]',
                'tr[onclick]'   # Clickable table rows
            ]
            
            listing_elements = []
            for selector in listing_selectors:
                elements = soup.select(selector)
                if elements:
                    listing_elements = elements
                    logger.debug(f"Using selector '{selector}' - found {len(elements)} elements")
                    break
            
            # Fallback: look for links that might be listings
            if not listing_elements:
                listing_elements = soup.select('a[href*="/item/"], a[href*="/product/"], a[href*="/listing/"], a[href*="/auction/"]')
                if listing_elements:
                    logger.debug(f"Fallback: found {len(listing_elements)} potential listing links")
            
            # If still no results, try more generic approach
            if not listing_elements:
                # Look for any container that might hold listings
                containers = soup.select('div[class*="result"], div[class*="item"], div[class*="product"], div[class*="listing"]')
                if containers:
                    listing_elements = containers[:20]  # Limit to reasonable number
                    logger.debug(f"Generic approach: found {len(listing_elements)} potential containers")
            
            # Parse individual listings
            for element in listing_elements[:50]:  # Limit to 50 results per page
                try:
                    listing = self._parse_single_listing(element, original_query)
                    if listing and listing.platform_id:  # Ensure we have a valid ID
                        listings.append(listing)
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