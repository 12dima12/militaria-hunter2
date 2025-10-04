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
from zoneinfo import ZoneInfo

from .base import BaseProvider
from models import Listing, SearchResult

logger = logging.getLogger(__name__)


class Militaria321Provider(BaseProvider):
    """Provider for militaria321.com"""
    
    def __init__(self):
        super().__init__("militaria321.com")
        self.base_url = "https://www.militaria321.com"
        self.search_url = f"{self.base_url}/search.cfm"
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
        
    async def search(self, keyword: str, since_ts: Optional[datetime] = None, sample_mode: bool = False, crawl_all: bool = False) -> SearchResult:
        """Search militaria321.com for listings"""
        try:
            query = self.build_query(keyword)
            
            # In sample_mode, fetch a few pages; in crawl_all, iterate until no more pages
            max_pages = 3 if sample_mode and not crawl_all else (1 if not crawl_all else 10_000)
            
            all_listings = []
            total_estimated = 0
            has_more = False
            pages_scanned_local = 0
            
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
                page = 1
                while page <= max_pages:
                    page_listings, page_total, page_has_more, soup, page_url = await self._fetch_page(client, query, page)
                    
                    if page_listings:
                        all_listings.extend(page_listings)
                        pages_scanned_local += 1
                        
                        # Update total estimate from first page that returns results
                        if page_total and total_estimated == 0:
                            total_estimated = page_total
                        
                        # If this page has more, overall result has more
                        if page_has_more:
                            has_more = True

                        # Provider-level early-stop: only when all items on this page have posted_ts and all are older than since_ts
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
                                    logger.info("Early-stop: page contains only items older than since_ts; stopping pagination for this run")
                                    break
                            except Exception:
                                pass
                    else:
                        # No results on this page, stop pagination
                        break
                    
                    # Small delay between pages to be respectful
                    if not crawl_all and page < max_pages:
                        await asyncio.sleep(1)
                    if crawl_all and not page_has_more:
                        break
                    page += 1
            
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
                has_more=has_more or len(unique_listings) >= 20,  # Assume more if we got many results
                pages_scanned=pages_scanned_local
            )
            
        except Exception as e:
            logger.error(f"Error searching militaria321 for '{keyword}': {e}")
            return SearchResult(items=[], total_count=0, has_more=False, pages_scanned=0)