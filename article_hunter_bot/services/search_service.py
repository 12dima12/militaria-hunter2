import logging
import re
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
    
    async def search_keyword(self, keyword: Keyword, dry_run: bool = False) -> List[Listing]:
        """Search for new items for a keyword subscription
        
        Returns only items that pass strict newness gating with deduplication
        """
        all_new_items = []
        seen_this_run = set()  # In-run deduplication
        
        # Get militaria321 provider
        provider = self.providers["militaria321.com"]
        
        try:
            # Search with polling mode (first page only)
            result = await provider.search(
                keyword=keyword.original_keyword,
                since_ts=keyword.since_ts,
                crawl_all=False  # Polling mode - page 1 only
            )
            
            # Build canonical listing keys and deduplicate in-run
            canonical_items = []
            for item in result.items:
                listing_key = self._build_canonical_listing_key(item)
                
                # Skip duplicates within this run
                if listing_key in seen_this_run:
                    logger.debug(f"Skipping in-run duplicate: {listing_key}")
                    continue
                
                seen_this_run.add(listing_key)
                
                # Update item with canonical key for consistency
                item.platform_id = listing_key.split(':', 1)[1]  # Extract ID part
                canonical_items.append(item)
            
            # Enrich militaria321 items with posted_ts/price if not in seen set
            unseen_items = []
            for item in canonical_items:
                listing_key = self._build_canonical_listing_key(item)
                if listing_key not in keyword.seen_listing_keys:
                    unseen_items.append(item)
            
            if unseen_items:
                # Fetch posted_ts and complete missing prices
                await provider.fetch_posted_ts_batch(unseen_items, concurrency=3)
            
            # Apply strict newness gating
            for item in canonical_items:
                listing_key = self._build_canonical_listing_key(item)
                
                # Check if already seen
                if listing_key in keyword.seen_listing_keys:
                    logger.info({
                        "event": "decision",
                        "platform": item.platform,
                        "listing_key": listing_key,
                        "posted_ts_utc": item.posted_ts.isoformat() if item.posted_ts else None,
                        "since_ts_utc": keyword.since_ts.isoformat(),
                        "decision": "already_seen",
                        "reason": "listing_key_in_seen_set"
                    })
                    continue
                
                # Apply newness gating
                if self._is_new_listing(item, keyword):
                    all_new_items.append(item)
                    
                    # Log decision
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
                    # Item fails newness gate but should be added to seen set
                    reason = self._get_filter_reason(item, keyword)
                    logger.info({
                        "event": "decision",
                        "platform": item.platform,
                        "listing_key": listing_key,
                        "posted_ts_utc": item.posted_ts.isoformat() if item.posted_ts else None,
                        "since_ts_utc": keyword.since_ts.isoformat(),
                        "decision": "absorbed",
                        "reason": reason
                    })
        
        except Exception as e:
            logger.error(f"Error searching {provider.platform_name}: {e}")
            
            # Update telemetry on error
            now = datetime.utcnow()
            keyword.last_checked = now
            keyword.last_error_ts = now
            keyword.consecutive_errors += 1
            keyword.last_error_message = str(e)[:500]
            
            # Update in database
            await self._update_keyword_telemetry(keyword)
            return all_new_items
        
        # Update telemetry on success
        now = datetime.utcnow()
        keyword.last_checked = now
        keyword.last_success_ts = now
        keyword.consecutive_errors = 0
        keyword.last_error_message = None
        
        # Update in database
        await self._update_keyword_telemetry(keyword)
        
        return all_new_items
    
    def _build_canonical_listing_key(self, item: Listing) -> str:
        """Build canonical listing key: militaria321.com:<numeric_id>"""
        # Ensure platform is lowercase and normalized
        platform = item.platform.lower().strip()
        
        # Extract numeric ID if platform_id contains extra data
        numeric_id = re.search(r'(\d+)', item.platform_id)
        if numeric_id:
            clean_id = numeric_id.group(1)
        else:
            clean_id = item.platform_id
        
        return f"{platform}:{clean_id}"
    
    async def _update_keyword_telemetry(self, keyword: Keyword):
        """Update keyword telemetry in database"""
        await self.db.db.keywords.update_one(
            {"id": keyword.id},
            {"$set": {
                "last_checked": keyword.last_checked,
                "last_success_ts": keyword.last_success_ts,
                "last_error_ts": keyword.last_error_ts,
                "last_error_message": keyword.last_error_message,
                "consecutive_errors": keyword.consecutive_errors,
                "baseline_status": keyword.baseline_status,
                "baseline_errors": keyword.baseline_errors,
                "updated_at": datetime.utcnow()
            }}
        )
    
    def compute_keyword_health(self, keyword: Keyword, now_utc: datetime, scheduler) -> tuple[str, str]:
        """Compute keyword health status and reason"""
        from zoneinfo import ZoneInfo
        
        def berlin(dt_utc: datetime | None) -> str:
            if not dt_utc:
                return "/"
            return dt_utc.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M") + " Uhr"
        
        INTERVAL_SEC = 60
        STALE_WARN_SEC = 180  # 3 minutes
        ERR_THRESHOLD = 3
        
        # Rule 1: Baseline not complete
        if keyword.baseline_status != "complete":
            status = "⏳ Baseline"
            reason = f"Status: {keyword.baseline_status}"
            if keyword.baseline_errors:
                first_error = next(iter(keyword.baseline_errors.values()))
                reason += f" - {first_error}"
            return status, reason
        
        # Rule 2: No scheduler job
        job_id = f"keyword_{keyword.id}"
        if not scheduler.scheduler_has_job(job_id):
            return "❌ Fehler", "Kein Scheduler-Job aktiv"
        
        # Rule 3: Too many consecutive errors
        if keyword.consecutive_errors >= ERR_THRESHOLD:
            reason = f"Letzte {ERR_THRESHOLD} Läufe fehlgeschlagen"
            if keyword.last_error_message:
                reason += f": {keyword.last_error_message[:100]}"
            return "❌ Fehler", reason
        
        # Rule 4: Never successful but has errors
        if keyword.last_success_ts is None and keyword.last_error_ts is not None:
            reason = "Noch kein erfolgreicher Lauf"
            if keyword.last_error_message:
                reason += f": {keyword.last_error_message[:100]}"
            return "❌ Fehler", reason
        
        # Rule 5: Stale success
        if keyword.last_success_ts and (now_utc - keyword.last_success_ts).total_seconds() > STALE_WARN_SEC:
            age_seconds = (now_utc - keyword.last_success_ts).total_seconds()
            if age_seconds < 3600:  # Less than 1 hour
                age = f"{int(age_seconds // 60)} Min"
            else:  # Hours
                age = f"{int(age_seconds // 3600)} Std"
            return "⚠️ Warnung", f"Zu lange kein Erfolg: letzte Prüfung vor {age}"
        
        # Rule 6: Healthy
        return "✅ Läuft", "Letzte Prüfung erfolgreich"
    
    async def full_baseline_crawl(self, keyword_text: str, keyword_id: Optional[str] = None) -> List[Listing]:
        """Perform full baseline crawl across ALL pages on militaria321.com
        
        Used for /search command to seed seen_listing_keys
        """
        # Update baseline status to running if keyword_id provided
        if keyword_id:
            await self.db.db.keywords.update_one(
                {"id": keyword_id},
                {"$set": {"baseline_status": "running", "baseline_errors": {}}}
            )
        
        provider = self.providers["militaria321.com"]
        
        try:
            # Crawl all pages
            result = await provider.search(
                keyword=keyword_text,
                crawl_all=True  # Baseline mode - all pages
            )
            
            # Update baseline status to complete if keyword_id provided
            if keyword_id:
                await self.db.db.keywords.update_one(
                    {"id": keyword_id},
                    {"$set": {
                        "baseline_status": "complete",
                        "baseline_errors": {},
                        "last_success_ts": datetime.utcnow(),
                        "consecutive_errors": 0
                    }}
                )
            
            logger.info(f"Baseline crawl for '{keyword_text}': {len(result.items)} items, {result.pages_scanned} pages")
            return result.items
        
        except Exception as e:
            logger.error(f"Error in baseline crawl: {e}")
            
            # Update baseline status to error if keyword_id provided
            if keyword_id:
                await self.db.db.keywords.update_one(
                    {"id": keyword_id},
                    {"$set": {
                        "baseline_status": "error",
                        "baseline_errors": {"militaria321.com": str(e)[:200]},
                        "last_error_ts": datetime.utcnow(),
                        "last_error_message": str(e)[:500]
                    }}
                )
            
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
