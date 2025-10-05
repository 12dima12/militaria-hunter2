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
    
    async def diagnose_keyword(self, keyword: Keyword, scheduler) -> str:
        """Comprehensive keyword diagnosis with German output"""
        from zoneinfo import ZoneInfo
        
        def berlin(dt_utc: datetime | None) -> str:
            if not dt_utc:
                return "/"
            return dt_utc.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M") + " Uhr"
        
        diagnosis_lines = [f"🔍 Diagnose für \"{keyword.original_keyword}\""]
        
        # 1. Baseline state analysis
        baseline_info = f"• Baseline: {keyword.baseline_status}"
        
        if keyword.baseline_pages_scanned:
            total_pages = sum(keyword.baseline_pages_scanned.values())
            baseline_info += f" — Seiten: {total_pages}"
        
        if keyword.baseline_items_collected:
            total_items = sum(keyword.baseline_items_collected.values())
            baseline_info += f" — Items: {total_items}"
        
        if keyword.baseline_errors:
            first_error = next(iter(keyword.baseline_errors.values()))
            baseline_info += f" — Fehler: {first_error[:50]}"
        else:
            baseline_info += " — Fehler: /"
        
        diagnosis_lines.append(baseline_info)
        
        # 2. Scheduler analysis
        job_id = f"keyword_{keyword.id}"
        has_job = scheduler.scheduler_has_job(job_id)
        
        if has_job:
            next_run = scheduler.get_job_next_run(job_id)
            next_run_str = berlin(next_run) if next_run else "unbekannt"
            scheduler_info = f"• Scheduler: vorhanden — Nächster Lauf: {next_run_str}"
        else:
            scheduler_info = "• Scheduler: ❌ FEHLT"
        
        diagnosis_lines.append(scheduler_info)
        
        # 3. Provider dry-run probe
        provider_results = {}
        
        for platform_name, provider in self.providers.items():
            try:
                # Probe first page only for diagnosis
                result = await provider.search(
                    keyword=keyword.original_keyword,
                    crawl_all=False  # Just first page for probe
                )
                
                # Count auction links by checking if items have platform_ids
                auctions_count = len([item for item in result.items if item.platform_id])
                parsed_count = len(result.items)
                
                # Check if query is reflected (basic check)
                query_reflected = parsed_count > 0  # If we got results, query probably worked
                
                provider_results[platform_name] = {
                    "ok": True,
                    "auctions": auctions_count, 
                    "parsed": parsed_count,
                    "query_reflected": query_reflected,
                    "reason": None
                }
                
                provider_info = (f"• Provider ({platform_name[:4]}): Seite 1 OK — "
                               f"Auktion-Links: {auctions_count} — Parser: {parsed_count} — "
                               f"Query reflektiert: {'ja' if query_reflected else 'nein'}")
                
            except Exception as e:
                error_reason = str(e)[:100]
                provider_results[platform_name] = {
                    "ok": False,
                    "auctions": 0,
                    "parsed": 0,
                    "query_reflected": False,
                    "reason": error_reason
                }
                
                provider_info = f"• Provider ({platform_name[:4]}): ❌ FEHLER — {error_reason}"
            
            diagnosis_lines.append(provider_info)
        
        # 4. Overall assessment
        if keyword.baseline_status == "complete" and has_job:
            if any(pr["ok"] for pr in provider_results.values()):
                assessment = "⇒ Status: Technisch gesund. Falls Probleme: prüfen Sie Netzwerk oder Anbieter-Änderungen."
            else:
                assessment = "⇒ Status: Provider-Probleme erkannt. Prüfen Sie Internetverbindung."
        elif keyword.baseline_status != "complete":
            assessment = f"⇒ Status: Baseline unvollständig ({keyword.baseline_status}). Warten oder /search erneut ausführen."
        elif not has_job:
            assessment = "⇒ Status: Scheduler-Job fehlt. Bot-Neustart erforderlich."
        else:
            assessment = "⇒ Status: Gemischte Probleme erkannt."
        
        diagnosis_lines.append(assessment)
        
        # Log diagnosis
        logger.info({
            "event": "kw_diagnose",
            "keyword_id": keyword.id,
            "baseline": keyword.baseline_status,
            "has_job": has_job,
            "provider_probe": provider_results
        })
        
        return "\n".join(diagnosis_lines)
    
    async def full_baseline_seed(self, keyword_text: str, keyword_id: str) -> tuple[List[Listing], dict]:
        """Perform full baseline crawl with proper state machine
        
        Implements baseline_status transitions: pending → running → complete/partial/error
        Returns: (all_items, last_item_meta)
        """
        now_utc = datetime.utcnow()
        
        # Start baseline: set status to running
        await self.db.db.keywords.update_one(
            {"id": keyword_id},
            {"$set": {
                "baseline_status": "running",
                "baseline_started_ts": now_utc,
                "baseline_errors": {},
                "baseline_pages_scanned": {},
                "baseline_items_collected": {}
            }}
        )
        
        all_items = []
        provider_results = {}
        provider_errors = {}
        last_item_meta = None
        
        # Process each provider
        for platform_name, provider in self.providers.items():
            try:
                logger.info(f"Starting baseline crawl for '{keyword_text}' on {platform_name}")
                
                # Crawl all pages for this provider
                result = await provider.search(
                    keyword=keyword_text,
                    crawl_all=True  # Baseline mode - all pages
                )
                
                provider_results[platform_name] = {
                    "pages_scanned": result.pages_scanned or 0,
                    "items_collected": len(result.items)
                }
                
                all_items.extend(result.items)
                
                # Track last item metadata for verification block
                if result.items and result.pages_scanned and result.pages_scanned > 0:
                    last_item_meta = {
                        "page_index": result.pages_scanned,
                        "listing": result.items[-1]  # Last item from last page
                    }
                
                logger.info(f"Baseline crawl completed for {platform_name}: "
                          f"{len(result.items)} items, {result.pages_scanned} pages")
                
            except Exception as e:
                error_msg = str(e)[:400]
                provider_errors[platform_name] = error_msg
                logger.error(f"Error in baseline crawl for {platform_name}: {error_msg}")
        
        # Determine final baseline status
        now_utc = datetime.utcnow()
        total_providers = len(self.providers)
        successful_providers = len(provider_results)
        
        if successful_providers == total_providers:
            final_status = "complete"
        elif successful_providers > 0:
            final_status = "partial" 
        else:
            final_status = "error"
        
        # Build update data
        baseline_pages_scanned = {p: r["pages_scanned"] for p, r in provider_results.items()}
        baseline_items_collected = {p: r["items_collected"] for p, r in provider_results.items()}
        
        # Update baseline completion
        update_data = {
            "baseline_status": final_status,
            "baseline_completed_ts": now_utc,
            "baseline_pages_scanned": baseline_pages_scanned,
            "baseline_items_collected": baseline_items_collected,
            "baseline_errors": provider_errors
        }
        
        # Set success telemetry if any providers succeeded
        if successful_providers > 0:
            update_data.update({
                "last_success_ts": now_utc,
                "consecutive_errors": 0,
                "last_error_message": None
            })
        
        await self.db.db.keywords.update_one({"id": keyword_id}, {"$set": update_data})
        
        # Structured baseline result log
        logger.info({
            "event": "baseline_result",
            "keyword_id": keyword_id,
            "status": final_status,
            "pages_scanned": baseline_pages_scanned,
            "items_collected": baseline_items_collected,
            "errors": provider_errors
        })
        
        return all_items, last_item_meta
    
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
