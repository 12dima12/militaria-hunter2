import logging
import unicodedata
from datetime import datetime, timedelta
from typing import List, Optional

from database import DatabaseManager
from models import Keyword, Listing, StoredListing
from providers.militaria321 import Militaria321Provider

logger = logging.getLogger(__name__)


class SearchService:
    """Core search service with strict newness logic"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        # Initialize with militaria321.com provider (extensible for future providers)
        self.providers = {
            "militaria321.com": Militaria321Provider()
        }
    
    async def search_keyword(self, keyword: Keyword) -> List[Listing]:
        """Search for new items for a keyword subscription
        
        Returns only items that pass strict newness gating
        """
        all_new_items = []
        
        # Get militaria321 provider
        provider = self.providers["militaria321.com"]
        
        try:
            # Search with polling mode (first page only)
            result = await provider.search(
                keyword=keyword.original_keyword,
                since_ts=keyword.since_ts,
                crawl_all=False  # Polling mode - page 1 only
            )
            
            # Enrich militaria321 items with posted_ts if not in seen set
            unseen_items = []
            for item in result.items:
                listing_key = f"{item.platform}:{item.platform_id}"
                if listing_key not in keyword.seen_listing_keys:
                    unseen_items.append(item)
            
            if unseen_items:
                # Fetch posted_ts for unseen militaria321 items
                await provider.fetch_posted_ts_batch(unseen_items, concurrency=4)
            
            # Apply strict newness gating
            for item in result.items:
                if self._is_new_listing(item, keyword):
                    all_new_items.append(item)
                    
                    # Log decision
                    listing_key = f"{item.platform}:{item.platform_id}"
                    logger.info({
                        "event": "decision",
                        "platform": item.platform,
                        "listing_key": listing_key,
                        "posted_ts_utc": item.posted_ts.isoformat() if item.posted_ts else None,
                        "since_ts_utc": keyword.since_ts.isoformat(),
                        "decision": "pushed",
                        "reason": "passed_newness_gate"
                    })
                else:
                    # Log why it was filtered out
                    listing_key = f"{item.platform}:{item.platform_id}"
                    reason = self._get_filter_reason(item, keyword)
                    logger.info({
                        "event": "decision",
                        "platform": item.platform,
                        "listing_key": listing_key,
                        "posted_ts_utc": item.posted_ts.isoformat() if item.posted_ts else None,
                        "since_ts_utc": keyword.since_ts.isoformat(),
                        "decision": reason,
                        "reason": "filtered_by_newness_gate"
                    })
        
        except Exception as e:
            logger.error(f"Error searching {provider.platform_name}: {e}")
        
        return all_new_items
    
    async def full_baseline_crawl(self, keyword_text: str) -> List[Listing]:
        """Perform full baseline crawl across ALL pages on militaria321.com
        
        Used for /search command to seed seen_listing_keys
        """
        provider = self.providers["militaria321.com"]
        
        try:
            # Crawl all pages
            result = await provider.search(
                keyword=keyword_text,
                crawl_all=True  # Baseline mode - all pages
            )
            
            logger.info(f"Baseline crawl for '{keyword_text}': {len(result.items)} items, {result.pages_scanned} pages")
            return result.items
        
        except Exception as e:
            logger.error(f"Error in baseline crawl: {e}")
            return []
    
    async def full_recheck_crawl(self, keyword_text: str) -> dict:
        """Perform full re-scan for /check command
        
        Returns page/item counts per provider
        """
        results = {}
        
        provider = self.providers["militaria321.com"]
        
        try:
            # Crawl all pages and update database
            result = await provider.search(
                keyword=keyword_text,
                crawl_all=True
            )
            
            # Store/update all listings in database
            for item in result.items:
                stored_listing = StoredListing(
                    platform=item.platform,
                    platform_id=item.platform_id,
                    title=item.title,
                    url=item.url,
                    price_value=item.price_value,
                    price_currency=item.price_currency,
                    image_url=item.image_url,
                    location=item.location,
                    condition=item.condition,
                    seller_name=item.seller_name,
                    posted_ts=item.posted_ts,
                    end_ts=item.end_ts
                )
                await self.db.upsert_listing(stored_listing)
            
            results[provider.platform_name] = {
                "pages_scanned": result.pages_scanned or 1,
                "total_count": len(result.items),
                "error": None
            }
            
        except Exception as e:
            logger.error(f"Error in recheck crawl: {e}")
            results[provider.platform_name] = {
                "pages_scanned": 0,
                "total_count": 0,
                "error": str(e)
            }
        
        return results
    
    def _is_new_listing(self, item: Listing, keyword: Keyword) -> bool:
        """Strict newness logic as specified in requirements
        
        Push only if ALL are true:
        1) Listing has posted_ts AND posted_ts >= since_ts (UTC-aware)
        2) listing_key not in seen_listing_keys
        3) Notification insert passes unique-idempotency guard (handled by NotificationService)
        
        If posted_ts missing: allow within 60-minute grace window after /search
        """
        listing_key = f"{item.platform}:{item.platform_id}"
        
        # Check if already seen
        if listing_key in keyword.seen_listing_keys:
            return False
        
        # Check posted_ts logic
        if item.posted_ts is not None:
            # Has posted_ts: must be >= since_ts
            return item.posted_ts >= keyword.since_ts
        else:
            # No posted_ts: allow within 60-minute grace window
            grace_window = timedelta(minutes=60)
            time_since_subscription = datetime.utcnow() - keyword.since_ts
            return time_since_subscription <= grace_window
    
    def _get_filter_reason(self, item: Listing, keyword: Keyword) -> str:
        """Get reason why item was filtered out"""
        listing_key = f"{item.platform}:{item.platform_id}"
        
        if listing_key in keyword.seen_listing_keys:
            return "already_seen"
        
        if item.posted_ts is not None:
            if item.posted_ts < keyword.since_ts:
                return "skipped_old"
        else:
            grace_window = timedelta(minutes=60)
            time_since_subscription = datetime.utcnow() - keyword.since_ts
            if time_since_subscription > grace_window:
                return "absorbed"
        
        return "unknown"
    
    @staticmethod
    def normalize_keyword(keyword: str) -> str:
        """Normalize keyword with Unicode NFKC as specified"""
        return unicodedata.normalize('NFKC', keyword.strip().lower())
