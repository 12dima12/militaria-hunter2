from typing import List, Dict, Any, Optional
import logging
from datetime import datetime, timedelta, timezone
import asyncio
import httpx
from bs4 import BeautifulSoup

from models import Listing, StoredListing, KeywordHit, Notification, Keyword
from database import DatabaseManager
from providers import get_all_providers
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class SearchService:
    """Service for searching across auction platforms"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        # Initialize providers from registry (in deterministic order)
        all_providers = get_all_providers()
        self.providers = {provider.name: provider for provider in all_providers}
        logger.info(f"Initialized SearchService with providers: {list(self.providers.keys())}")
        self.notification_service = NotificationService(db_manager)
    
    async def search_keyword(self, keyword: Keyword) -> Dict[str, Any]:
        """Search for a keyword and notify only for truly new listings"""
        results = {
            "keyword_id": keyword.id,
            "keyword_text": keyword.keyword,
            "new_notifications": 0,
            "matched_listings": 0,
            "total_raw_listings": 0,
            "errors": []
        }
        
        try:
            # Search each platform
            all_raw_listings = []
            
            for platform in keyword.platforms:
                if platform in self.providers:
                    try:
                        provider = self.providers[platform]
                        search_result = await provider.search(keyword.keyword, sample_mode=False)
                        all_raw_listings.extend(search_result.items)
                        logger.debug(f"Raw search found {len(search_result.items)} listings for '{keyword.keyword}' on {platform}")
                        
                    except Exception as e:
                        error_msg = f"Error searching {platform}: {str(e)}"
                        results["errors"].append(error_msg)
                        logger.error(error_msg)
                else:
                    error_msg = f"Provider not found for platform: {platform}"
                    results["errors"].append(error_msg)
                    logger.warning(error_msg)
            
            results["total_raw_listings"] = len(all_raw_listings)
            
            # Apply title-only matching to get relevant listings
            matched_listings = []
            provider = self.providers[keyword.platforms[0]]  # Use first platform's provider for matching
            
            for listing in all_raw_listings:
                if provider.matches_keyword(listing.title, keyword.keyword):
                    matched_listings.append(listing)
            
            results["matched_listings"] = len(matched_listings)
            logger.info(f"Keyword '{keyword.keyword}': {len(all_raw_listings)} raw -> {len(matched_listings)} matched")
            
            # Check for truly new listings (not in seen_set and posted after since_ts)
            new_notifications = []
            
            from services.keyword_service import KeywordService
            keyword_service = KeywordService(self.db)
            
            for listing in matched_listings:
                listing_key = keyword_service.make_listing_key(listing.platform, listing.platform_id)
                
                # Skip if already seen
                if keyword_service.is_listing_seen(keyword, listing.platform, listing.platform_id):
                    continue
                
                # Check if listing was posted after subscription start
                # Since we don't have posting timestamp from militaria321, assume first_seen is posting time
                is_new_posting = True
                if listing.first_seen_ts and listing.first_seen_ts <= keyword.since_ts:
                    is_new_posting = False
                
                # For listings without timestamp, consider them new if not in seen_set
                if is_new_posting or not listing.first_seen_ts:
                    # Store listing in database
                    stored_listing = StoredListing(
                        platform=listing.platform,
                        platform_id=listing.platform_id,
                        title=listing.title,
                        url=listing.url,
                        price_value=listing.price_value,
                        price_currency=listing.price_currency,
                        location=listing.location,
                        condition=listing.condition,
                        seller_name=listing.seller_name,
                        seller_rating=listing.seller_rating,
                        listing_type=listing.listing_type,
                        image_url=listing.image_url,
                        first_seen_ts=listing.first_seen_ts or datetime.utcnow(),
                        last_seen_ts=listing.last_seen_ts or datetime.utcnow()
                    )
                    
                    await self.db.create_or_update_listing(stored_listing)
                    new_notifications.append(stored_listing)
                    
                    # Add to seen_set
                    await keyword_service.add_to_seen_set(keyword.id, listing.platform, listing.platform_id)
                    
                    logger.info(f"New listing to notify: {listing.title}")
                
                else:
                    # Add to seen_set even if we don't notify (it's an old item we just discovered)
                    await keyword_service.add_to_seen_set(keyword.id, listing.platform, listing.platform_id)
            
            results["new_notifications"] = len(new_notifications)
            
            # Send notifications for truly new listings only
            if new_notifications and not keyword.is_muted:
                await self._send_notifications(keyword, new_notifications)
            
            # Update last checked timestamp
            await keyword_service.update_last_checked(keyword.id)
            
        except Exception as e:
            error_msg = f"Error searching keyword '{keyword.keyword}': {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)
        
        return results
    
    async def _send_notifications(self, keyword: Keyword, listings: List[StoredListing]):
        """Send notifications for new listings"""
        try:
            # Get user
            user = await self.db.get_user_by_id(keyword.user_id)
            if not user:
                logger.error(f"User not found for keyword {keyword.id}")
                return
            
            # Send notifications
            for listing in listings:
                await self.notification_service.send_listing_notification(
                    user, keyword, listing
                )
                
        except Exception as e:
            logger.error(f"Error sending notifications: {e}")
    
    async def search_all_active_keywords(self) -> Dict[str, Any]:
        """Search all active keywords"""
        results = {
            "keywords_processed": 0,
            "total_new_listings": 0,
            "errors": [],
            "start_time": datetime.utcnow()
        }
        
        try:
            # Get all active keywords
            keywords = await self.db.get_all_active_keywords()
            logger.info(f"Processing {len(keywords)} active keywords")
            
            # Process keywords in batches to avoid overwhelming providers
            batch_size = 5
            for i in range(0, len(keywords), batch_size):
                batch = keywords[i:i + batch_size]
                tasks = [self.search_keyword(keyword) for keyword in batch]
                
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for keyword, result in zip(batch, batch_results):
                    if isinstance(result, Exception):
                        error_msg = f"Exception processing keyword '{keyword.keyword}': {str(result)}"
                        results["errors"].append(error_msg)
                        logger.error(error_msg)
                    else:
                        results["keywords_processed"] += 1
                        results["total_new_listings"] += result.get("new_listings", 0)
                        results["errors"].extend(result.get("errors", []))
                
                # Small delay between batches
                if i + batch_size < len(keywords):
                    await asyncio.sleep(2)
            
            results["end_time"] = datetime.utcnow()
            results["duration_seconds"] = (results["end_time"] - results["start_time"]).total_seconds()
            
            logger.info(f"Processed {results['keywords_processed']} keywords, "
                       f"found {results['total_new_listings']} new listings in "
                       f"{results['duration_seconds']:.1f} seconds")
            
        except Exception as e:
            error_msg = f"Error in search_all_active_keywords: {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)
        

    async def get_counts_per_provider(self, keyword_text: str, providers_filter: List[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Get counts per provider for a keyword (for /suche).
        Returns: {platform: {matched_count, total_count, has_more, error}}
        """
        if providers_filter is None:
            providers_filter = list(self.providers.keys())
        
        results = {}
        
        for platform in providers_filter:
            if platform not in self.providers:
                results[platform] = {
                    "matched_count": 0,
                    "total_count": None,
                    "has_more": False,
                    "error": f"Provider {platform} not found"
                }
                continue
            
            try:
                provider = self.providers[platform]
                
                # Search with sample_mode to get better counts
                search_result = await provider.search(keyword_text, sample_mode=True)
                
                # Apply title-only matching
                matched_items = []
                for item in search_result.items:
                    if provider.matches_keyword(item.title, keyword_text):
                        matched_items.append(item)
                
                results[platform] = {
                    "matched_count": len(matched_items),
                    "total_count": search_result.total_count,
                    "has_more": search_result.has_more,
                    "error": None,
                    "items": matched_items  # Include items for baseline seeding
                }
                
                logger.info(f"get_counts_per_provider({keyword_text}, {platform}): {len(matched_items)} matched")
                
            except Exception as e:
                logger.error(f"Error getting counts for {platform}: {e}")
                results[platform] = {
                    "matched_count": 0,
                    "total_count": None,
                    "has_more": False,
                    "error": str(e),
                    "items": []
                }
        
        return results
    
    async def get_sample_blocks(self, keyword_text: str, providers_filter: List[str] = None, seed_baseline: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        Get sample blocks per provider (for /testen).
        Returns: {platform: {matched_items (top 3), total_count, has_more, error}}
        If seed_baseline=True, will also return all items for seeding.
        """
        if providers_filter is None:
            providers_filter = list(self.providers.keys())
        
        results = {}
        
        for platform in providers_filter:
            if platform not in self.providers:
                results[platform] = {
                    "matched_items": [],
                    "total_count": None,
                    "has_more": False,
                    "error": f"Provider {platform} not found"
                }
                continue
            
            try:
                provider = self.providers[platform]
                
                # Search with sample_mode
                search_result = await provider.search(keyword_text, sample_mode=True)
                
                # Apply title-only matching
                matched_items = []
                for item in search_result.items:
                    if provider.matches_keyword(item.title, keyword_text):
                        matched_items.append(item)
                
                # Return top 3 for display
                results[platform] = {
                    "matched_items": matched_items[:3],  # Top 3 for display
                    "all_items": matched_items if seed_baseline else [],  # All items if seeding
                    "total_count": search_result.total_count,
                    "has_more": search_result.has_more or len(matched_items) > 3,
                    "error": None,
                    "provider": provider  # Include provider for price formatting
                }
                
                logger.info(f"get_sample_blocks({keyword_text}, {platform}): {len(matched_items)} matched, showing top 3")
                
            except Exception as e:
                logger.error(f"Error getting samples for {platform}: {e}")
                results[platform] = {
                    "matched_items": [],
                    "all_items": [],
                    "total_count": None,
                    "has_more": False,
                    "error": str(e),
                    "provider": self.providers.get(platform)
                }
        
        return results

    async def full_baseline_seed(self, keyword_text: str, keyword_id: str, user_id: str, providers_filter: List[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Perform full baseline seeding across ALL pages for each provider.
        
        This crawls every page of search results and adds all listing keys to seen_set.
        NO notifications are sent during seeding.
        
        Returns: {platform: {pages_scanned, items_collected, keys_added, duration_ms, error}}
        """
        import time
        import random
        
        if providers_filter is None:
            providers_filter = list(self.providers.keys())
        
        results = {}
        SEED_HARD_CAP = 5000  # Global page limit to prevent runaway
        total_pages_scanned = 0
        
        for platform in providers_filter:
            if platform not in self.providers:
                results[platform] = {
                    "pages_scanned": 0,
                    "items_collected": 0,
                    "keys_added": 0,
                    "duration_ms": 0,
                    "error": f"Provider {platform} not found"
                }
                continue
            
            start_time = time.time()
            provider = self.providers[platform]
            pages_scanned = 0
            items_collected = 0
            keys_added_count = 0
            visited_urls = set()  # Loop detection
            
            try:
                # Build initial search URL
                query = provider.build_query(keyword_text)
                
                # Start with first page
                current_url = None
                if platform == "militaria321.com":
                    params = {'q': query}
                    from urllib.parse import urlencode
                    current_url = f"{provider.search_url}?{urlencode(params)}"
                elif platform == "egun.de":
                    params = {
                        'mode': 'qry',
                        'plusdescr': 'off',
                        'wheremode': 'and',
                        'query': query,
                        'quick': '1'
                    }
                    from urllib.parse import urlencode
                    current_url = f"{provider.search_url}?{urlencode(params)}"
                else:
                    # Generic fallback
                    current_url = provider.search_url
                
                logger.info(f"Starting full baseline seed for '{keyword_text}' on {platform}")
                
                # Persistent HTTP client
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Accept-Encoding': 'br, gzip, deflate',
                }
                
                async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
                    while current_url and pages_scanned < SEED_HARD_CAP:
                        # Check for loop
                        if current_url in visited_urls:
                            logger.warning(f"Pagination loop detected at {current_url}, stopping")
                            break
                        visited_urls.add(current_url)
                        
                        # Rate limiting with jitter
                        if pages_scanned > 0:
                            jitter = random.uniform(0.25, 0.75)
                            await asyncio.sleep(jitter)
                        
                        try:
                            # Fetch page with retries on rate limit
                            max_retries = 3
                            for attempt in range(max_retries):
                                try:
                                    response = await client.get(current_url)
                                    
                                    if response.status_code == 429:
                                        # Rate limited
                                        backoff = (2 ** attempt) * 0.5
                                        logger.warning(f"Rate limited on {platform}, backing off {backoff}s")
                                        await asyncio.sleep(backoff)
                                        continue
                                    
                                    response.raise_for_status()
                                    break
                                    
                                except httpx.HTTPStatusError as e:
                                    if e.response.status_code >= 500 and attempt < max_retries - 1:
                                        backoff = (2 ** attempt) * 0.5
                                        logger.warning(f"Server error on {platform}, retrying in {backoff}s")
                                        await asyncio.sleep(backoff)
                                        continue
                                    raise
                            
                            # Set encoding
                            if response.encoding is None or response.encoding.lower() == 'iso-8859-1':
                                response.encoding = 'utf-8'
                            
                            # Parse HTML
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(response.text, 'html.parser')
                            
                            # Parse items on this page
                            page_items, _, _ = provider._parse_search_page(soup, keyword_text, pages_scanned + 1)
                            
                            items_on_page = len(page_items)
                            items_collected += items_on_page
                            
                            # Batch add to seen_set
                            if page_items:
                                keys_batch = [f"{platform}:{item.platform_id}" for item in page_items]
                                
                                # Add to database in batch
                                await self.db.add_to_seen_set_batch(keyword_id, keys_batch)
                                keys_added_count += len(keys_batch)
                            
                            pages_scanned += 1
                            total_pages_scanned += 1
                            
                            logger.debug(f"{platform} page {pages_scanned}: {items_on_page} items, total collected: {items_collected}")
                            
                            # Get next page URL
                            next_url = provider._get_next_page_url(current_url, soup)
                            
                            if next_url:
                                logger.debug(f"{platform} next page: {next_url}")
                                current_url = next_url
                            else:
                                logger.info(f"{platform} reached last page at page {pages_scanned}")
                                break
                            
                            # Global hard cap check
                            if total_pages_scanned >= SEED_HARD_CAP:
                                logger.warning(f"Hit global SEED_HARD_CAP of {SEED_HARD_CAP} pages, stopping all seeding")
                                break
                        
                        except Exception as e:
                            logger.error(f"Error fetching page {pages_scanned + 1} for {platform}: {e}")
                            # Continue with next page or stop
                            break
                
                duration_ms = int((time.time() - start_time) * 1000)
                
                results[platform] = {
                    "pages_scanned": pages_scanned,
                    "items_collected": items_collected,
                    "keys_added": keys_added_count,
                    "duration_ms": duration_ms,
                    "error": None
                }
                
                logger.info(f"Baseline seed complete for {platform}: {pages_scanned} pages, {items_collected} items, {keys_added_count} keys added, {duration_ms}ms")
                
            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)
                logger.error(f"Error seeding baseline for {platform}: {e}")
                results[platform] = {
                    "pages_scanned": pages_scanned,
                    "items_collected": items_collected,
                    "keys_added": keys_added_count,
                    "duration_ms": duration_ms,
                    "error": str(e)
                }
        
        return results

        return results