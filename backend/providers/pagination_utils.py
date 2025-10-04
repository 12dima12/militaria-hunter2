"""
Pagination utilities for provider implementations
"""
import re
from urllib.parse import urljoin, urlencode, urlparse, parse_qs, urlunparse
from typing import Optional, List
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)


def replace_query_param(url: str, key: str, value: str) -> str:
    """Replace or add a query parameter in URL"""
    u = urlparse(url)
    q = parse_qs(u.query, keep_blank_values=True)
    q[key] = [value]
    # Preserve order and structure
    new_q = urlencode([(k, v) for k, vals in q.items() for v in vals])
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))


def first_numeric_greater_than(soup: BeautifulSoup, current: int, selectors: List[str]) -> Optional[int]:
    """
    Find the first numeric page number greater than current from given selectors.
    Returns the smallest page number > current, or None if none found.
    """
    nums = []
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get('href') or ''
            # Try both 'page' and 'seite' parameters
            for pattern in [r'[?&]page=(\d+)', r'[?&]seite=(\d+)', r'[?&]start=(\d+)']:
                m = re.search(pattern, href)
                if m:
                    n = int(m.group(1))
                    if n > current:
                        nums.append(n)
                    break  # Don't try other patterns for same href
    
    return min(nums) if nums else None


def get_next_page_url_militaria321(current_url: str, soup: BeautifulSoup) -> Optional[str]:
    """
    Militaria321-specific next page detection.
    
    Strategy:
    1. Look for explicit next-page links (rel="next", class="next", etc.)
    2. Fall back to numeric page detection
    3. Support both 'seite=' and 'page=' parameters
    """
    # Try direct next-page selectors first
    selectors = [
        'a[rel="next"]',
        'a.next',
        'a.pager_next',
        '.pagination a[title*="weiter"]',
        '.pagination a:contains("weiter")',
        '.pagination a:contains("nächste")',
        '.seiten a:contains("nächste")',
        'a:contains("›")',
        'a:contains("»")'
    ]
    
    for selector in selectors:
        try:
            a = soup.select_one(selector)
            if a and a.get('href'):
                next_url = urljoin(current_url, a['href'])
                logger.debug(f"Found next page via selector '{selector}': {next_url}")
                return next_url
        except Exception as e:
            logger.debug(f"Selector '{selector}' failed: {e}")
            continue
    
    # Fallback: numeric page detection
    # Extract current page number from URL
    cur = 1
    for pattern in [r'[?&]seite=(\d+)', r'[?&]page=(\d+)']:
        m = re.search(pattern, current_url)
        if m:
            cur = int(m.group(1))
            break
    
    # Find next page number
    page_selectors = [
        '.pagination a',
        '.seiten a',
        '.pager a',
        'a[href*="seite="]',
        'a[href*="page="]'
    ]
    
    nxt = first_numeric_greater_than(soup, cur, page_selectors)
    
    if nxt:
        # Determine which parameter to use
        if 'seite=' in current_url:
            next_url = replace_query_param(current_url, 'seite', str(nxt))
        else:
            next_url = replace_query_param(current_url, 'page', str(nxt))
        logger.debug(f"Next page via numeric detection: {next_url} (current={cur}, next={nxt})")
        return next_url
    
    logger.debug(f"No next page found for militaria321 (current page: {cur})")
    return None


def get_next_page_url_egun(current_url: str, soup: BeautifulSoup) -> Optional[str]:
    """
    eGun-specific next page detection.
    
    Strategy:
    1. Look for explicit next-page links
    2. Fall back to numeric 'page=' parameter detection
    3. Preserve all other query params (mode, query, etc.)
    """
    # Try direct next-page selectors
    selectors = [
        'a[rel="next"]',
        '.pagination a.next',
        '.pager a.next',
        'a:contains("weiter")',
        'a:contains("›")',
        'a:contains("»")'
    ]
    
    for selector in selectors:
        try:
            a = soup.select_one(selector)
            if a and a.get('href'):
                next_url = urljoin(current_url, a['href'])
                logger.debug(f"Found next page via selector '{selector}': {next_url}")
                return next_url
        except Exception as e:
            logger.debug(f"Selector '{selector}' failed: {e}")
            continue
    
    # Fallback: numeric page detection
    # eGun typically uses 'start=' parameter for pagination (offset-based)
    # Extract current start value
    cur_start = 0
    m = re.search(r'[?&]start=(\d+)', current_url)
    if m:
        cur_start = int(m.group(1))
    
    # Look for links with higher start values
    page_selectors = [
        '.pagination a',
        '.pager a',
        'a[href*="start="]'
    ]
    
    nums = []
    for sel in page_selectors:
        for a in soup.select(sel):
            href = a.get('href') or ''
            m = re.search(r'[?&]start=(\d+)', href)
            if m:
                n = int(m.group(1))
                if n > cur_start:
                    nums.append(n)
    
    if nums:
        next_start = min(nums)
        next_url = replace_query_param(current_url, 'start', str(next_start))
        logger.debug(f"Next page via numeric detection: {next_url} (current start={cur_start}, next start={next_start})")
        return next_url
    
    logger.debug(f"No next page found for egun (current start: {cur_start})")
    return None


def get_next_page_url_generic(current_url: str, soup: BeautifulSoup) -> Optional[str]:
    """
    Generic fallback next-page detection for any provider.
    """
    # Try rel="next" first (standard)
    a = soup.select_one('a[rel="next"]')
    if a and a.get('href'):
        return urljoin(current_url, a['href'])
    
    # Look in pagination containers
    for container_sel in ['.pagination', '.pager', '.seiten', 'nav[aria-label*="Seite"]']:
        container = soup.select_one(container_sel)
        if not container:
            continue
        
        # Look for common "next" text patterns
        for text_pattern in ['weiter', 'nächste', '›', '»', 'next']:
            for a in container.select('a'):
                if text_pattern in a.get_text().lower():
                    href = a.get('href')
                    if href:
                        return urljoin(current_url, href)
    
    return None
