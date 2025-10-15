import logging
import os
import re
import unicodedata
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Literal

from database import DatabaseManager
from models import Keyword, Listing, StoredListing
from providers import get_all_providers
from utils.text import br_join, b, i, a, code, fmt_ts_de, fmt_price_de
from utils.datetime_utils import now_utc as get_utc_now, to_utc_aware

logger = logging.getLogger(__name__)

# Configuration constants with environment variable support
_RAW_POLL_MODE = os.environ.get("POLL_MODE", "full").strip().lower()
if _RAW_POLL_MODE not in {"", "full"}:
    logger.warning(
        "Rotate poll mode has been disabled. Falling back to full-scan pagination."
    )
POLL_MODE = "full"
PRIMARY_PAGES = int(os.environ.get("PRIMARY_PAGES", "1"))
POLL_WINDOW = int(os.environ.get("POLL_WINDOW", "5"))
MAX_PAGES_PER_CYCLE = int(os.environ.get("MAX_PAGES_PER_CYCLE", "200"))  # Allow scanning up to 200 pages
DETAIL_CONCURRENCY = int(os.environ.get("DETAIL_CONCURRENCY", "4"))
GRACE_MINUTES = int(os.environ.get("GRACE_MINUTES", "60"))
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))


class SearchService:
    """Core search service with strict newness logic"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        # Initialize all registered providers in deterministic order
        self.providers = get_all_providers()
        self.provider_status: dict[str, dict] = {name: {} for name in self.providers}
        self.notification_service = None

    def attach_notification_service(self, notification_service) -> None:
        """Attach notification service for admin diagnostics"""
        self.notification_service = notification_service
    
    async def search_keyword(self, keyword: Keyword, dry_run: bool = False) -> List[Listing]:
        """Search for new items with the deep pagination strategy.

        The system now relies on the full-scan mode for every keyword to guarantee
        that no militaria321.com listings are missed due to end-date sorting quirks.
        Legacy "rotate" mode definitions are migrated automatically to the
        full-scan behaviour.
        """
        # Check if keyword needs migration (empty or non-ID-based seen_listing_keys)
        needs_migration = False
        if not keyword.seen_listing_keys:
            logger.info(f"Keyword {keyword.normalized_keyword} has empty seen_listing_keys - triggering migration")
            needs_migration = True
        elif len(keyword.seen_listing_keys) < 10:  # Suspiciously small for militaria321
            # Check if keys are ID-based
            non_id_keys = [k for k in keyword.seen_listing_keys[:5] if not (k.startswith('militaria321.com:') and k.split(':', 1)[1].isdigit())]
            if non_id_keys:
                logger.info(f"Keyword {keyword.normalized_keyword} has non-ID-based seen_listing_keys - triggering migration")
                needs_migration = True
        
        if needs_migration:
            try:
                await self.reseed_seen_keys_for_keyword(keyword.id)
                # Reload keyword after migration
                doc = await self.db.db.keywords.find_one({"id": keyword.id})
                keyword = Keyword(**doc)
                logger.info(f"Migration completed for {keyword.normalized_keyword}: {len(keyword.seen_listing_keys)} keys")
            except Exception as e:
                logger.error(f"Migration failed for {keyword.normalized_keyword}: {e}")
                # Continue with polling even if migration fails
        
        all_new_items = []
        seen_this_run = set()  # In-run deduplication

        await self._ensure_keyword_platforms(keyword)
        since_ts_utc = self._normalize_since_ts(keyword)

        # Determine polling mode: rotate has been disabled globally; ensure keywords follow suit
        stored_poll_mode = getattr(keyword, 'poll_mode', POLL_MODE)
        normalized_poll_mode = (stored_poll_mode or "").lower()
        if normalized_poll_mode != "full":
            logger.info(
                {
                    "event": "poll_mode_reset",
                    "keyword": keyword.normalized_keyword,
                    "previous_mode": stored_poll_mode,
                    "new_mode": "full",
                }
            )
            keyword.poll_mode = "full"
            keywords_collection = getattr(getattr(self.db, "db", None), "keywords", None)
            if keywords_collection is not None:
                await keywords_collection.update_one(
                    {"id": keyword.id},
                    {"$set": {"poll_mode": "full"}},
                )

        poll_mode = "full"
        poll_window = getattr(keyword, 'poll_window', POLL_WINDOW)
        total_pages_estimate = getattr(keyword, 'total_pages_estimate', None)
        poll_cursor_page = getattr(keyword, 'poll_cursor_page', 1)
        
        # Get militaria321 provider
        militaria_provider = self.providers["militaria321.com"]
        
        try:
            # Determine which pages to scan based on polling mode
            pages_to_scan = self._determine_pages_to_scan(
                poll_mode, poll_cursor_page, poll_window, 
                total_pages_estimate, PRIMARY_PAGES, MAX_PAGES_PER_CYCLE
            )
            
            logger.info({
                "event": "poll_start",
                "keyword": keyword.normalized_keyword,
                "mode": poll_mode,
                "pages_to_scan": pages_to_scan,
                "cursor_start": poll_cursor_page,
                "window_size": poll_window
            })
            
            # Use provider's built-in crawl_all mode to scan all available pages efficiently
            max_pages_to_scan = min(len(pages_to_scan), MAX_PAGES_PER_CYCLE)
            
            result = await militaria_provider.search(
                keyword=keyword.original_keyword,
                since_ts=since_ts_utc,
                crawl_all=True,  # Scan ALL pages to prevent missed items
                max_pages_override=max_pages_to_scan
            )

            await self._handle_provider_metadata(
                militaria_provider.platform_name, result.metadata, keyword
            )
            
            militaria_pages_scanned = result.pages_scanned or 0
            all_items = result.items
            unseen_candidates = 0
            pushed_count = 0
            absorbed_count = 0
            
            # Process and deduplicate all items
            unique_items = []
            for item in all_items:
                listing_key = self._build_canonical_listing_key(item)

                # Skip duplicates within this run
                if listing_key in seen_this_run:
                    continue

                seen_this_run.add(listing_key)

                # Update item with canonical key for consistency
                item.platform_id = listing_key.split(':', 1)[1]  # Extract ID part
                self._normalize_listing_timestamp(item)
                unique_items.append(item)
                
                # Track unseen candidates (not in baseline)
                if listing_key not in keyword.seen_listing_keys:
                    unseen_candidates += 1
            
            all_items = unique_items
            
            logger.info({
                "event": "deep_scan_complete",
                "q": keyword.normalized_keyword,
                "pages_scanned": militaria_pages_scanned,
                "total_items_found": len(all_items),
                "unseen_candidates": unseen_candidates,
                "max_pages_allowed": max_pages_to_scan
            })

            # Collect results from additional providers (e.g., egun.de)
            for platform_name, provider in self.providers.items():
                if platform_name == "militaria321.com":
                    continue
                if platform_name not in getattr(keyword, "platforms", [platform_name]):
                    continue

                try:
                    provider_result = await provider.search(
                        keyword=keyword.original_keyword,
                        since_ts=since_ts_utc,
                        crawl_all=True
                    )
                    await self._handle_provider_metadata(
                        platform_name, provider_result.metadata, keyword
                    )
                except Exception as exc:
                    logger.error(f"Error searching {platform_name}: {exc}")
                    continue

                logger.info(
                    {
                        "event": "provider_summary",
                        "platform": platform_name,
                        "q": keyword.normalized_keyword,
                        "pages_scanned": provider_result.pages_scanned,
                        "items_found": len(provider_result.items),
                    }
                )

                for item in provider_result.items:
                    listing_key = self._build_canonical_listing_key(item)
                    if listing_key in seen_this_run:
                        continue
                    seen_this_run.add(listing_key)
                    if listing_key not in keyword.seen_listing_keys:
                        unseen_candidates += 1
                    all_items.append(item)

            # Enrich unseen items with posted_ts/price
            unseen_items = []
            for item in all_items:
                listing_key = self._build_canonical_listing_key(item)
                if listing_key not in keyword.seen_listing_keys:
                    unseen_items.append(item)

            if unseen_items:
                unseen_by_platform: dict[str, List[Listing]] = {}
                for item in unseen_items:
                    unseen_by_platform.setdefault(item.platform, []).append(item)

                for platform_name, items in unseen_by_platform.items():
                    provider = self.providers.get(platform_name)
                    if provider and hasattr(provider, "fetch_posted_ts_batch"):
                        try:
                            await provider.fetch_posted_ts_batch(items, concurrency=DETAIL_CONCURRENCY)
                        except Exception as exc:
                            logger.warning(f"Detail fetch failed for {platform_name}: {exc}")
                    self._apply_posted_ts_fallback(items)
            
            # Apply strict newness gating to all collected items
            new_seen_keys = []
            for item in all_items:
                listing_key = self._build_canonical_listing_key(item)
                
                # Check if already seen
                if listing_key in keyword.seen_listing_keys:
                    continue
                
                # Add to seen set regardless of newness (idempotent baseline expansion)
                new_seen_keys.append(listing_key)
                
                # Apply newness gating for push notifications
                if self._is_new_listing(item, keyword):
                    all_new_items.append(item)
                    pushed_count += 1
                    
                    # Log decision with detailed reason
                    if item.posted_ts is not None:
                        reason = "posted_ts>=since_ts"
                    else:
                        reason = "grace_window_allowed"
                    
                    logger.info({
                        "event": "decision",
                        "platform": item.platform,
                        "keyword_norm": keyword.normalized_keyword,
                        "listing_key": listing_key,
                        "posted_ts_utc": item.posted_ts.isoformat() if item.posted_ts else None,
                        "since_ts_utc": keyword.since_ts.isoformat(),
                        "decision": "pushed",
                        "reason": reason
                    })
                else:
                    absorbed_count += 1
                    # Item fails newness gate but should be added to seen set
                    reason = self._get_filter_reason(item, keyword)
                    logger.info({
                        "event": "decision",
                        "platform": item.platform,
                        "keyword_norm": keyword.normalized_keyword,
                        "listing_key": listing_key,
                        "posted_ts_utc": item.posted_ts.isoformat() if item.posted_ts else None,
                        "since_ts_utc": keyword.since_ts.isoformat(),
                        "decision": "absorbed",
                        "reason": reason
                    })
            
            # Update seen_listing_keys in database
            if new_seen_keys:
                await self.db.db.keywords.update_one(
                    {"id": keyword.id},
                    {"$addToSet": {"seen_listing_keys": {"$each": new_seen_keys}}}
                )
            
            # Update poll cursor for rotating mode
            if poll_mode == "rotate" and militaria_pages_scanned > 0:
                new_cursor = (poll_cursor_page + poll_window)
                if total_pages_estimate and new_cursor > total_pages_estimate:
                    new_cursor = 1  # Wrap around
                
                await self.db.db.keywords.update_one(
                    {"id": keyword.id},
                    {"$set": {"poll_cursor_page": new_cursor}}
                )
            
            # Log poll summary
            logger.info({
                "event": "poll_summary",
                "keyword": keyword.normalized_keyword,
                "mode": poll_mode,
                "pages_scanned": militaria_pages_scanned,
                "primary_pages": PRIMARY_PAGES,
                "cursor_start": poll_cursor_page,
                "window_size": poll_window,
                "unseen_candidates": unseen_candidates,
                "pushed": pushed_count,
                "absorbed": absorbed_count
            })
            
            # Update total_pages_estimate if missing (for proper rotating deep-scan)
            if total_pages_estimate is None and militaria_pages_scanned > 0:
                # Estimate based on baseline data or use a reasonable default
                baseline_pages = getattr(keyword, 'baseline_pages_scanned', {})
                militaria_pages = baseline_pages.get('militaria321.com', 0)
                
                if militaria_pages > 0:
                    estimated_pages = militaria_pages
                else:
                    # Conservative estimate based on cursor position
                    estimated_pages = max(poll_cursor_page + 50, 100)
                
                # Update in database
                await self.db.db.keywords.update_one(
                    {"id": keyword.id},
                    {"$set": {"total_pages_estimate": estimated_pages}}
                )
                logger.info(f"Updated total_pages_estimate for '{keyword.normalized_keyword}': {estimated_pages} (was None)")
            
        except Exception as e:
            logger.error(f"Error in deep polling for {militaria_provider.platform_name}: {e}")
            
            # Update telemetry on error
            now = get_utc_now()
            keyword.last_checked = now
            keyword.last_error_ts = now
            keyword.consecutive_errors += 1
            keyword.last_error_message = str(e)[:500]
            
            # Update in database
            await self._update_keyword_telemetry(keyword)
            return all_new_items
        
        # Update telemetry on success
        now = get_utc_now()
        keyword.last_checked = now
        keyword.last_success_ts = now
        keyword.consecutive_errors = 0
        keyword.last_error_message = None
        
        # Update in database
        await self._update_keyword_telemetry(keyword)
        
        return all_new_items
    
    def _determine_pages_to_scan(
        self, 
        poll_mode: str, 
        cursor_page: int, 
        window_size: int, 
        total_pages_estimate: Optional[int], 
        primary_pages: int, 
        max_pages_per_cycle: int
    ) -> List[int]:
        """Determine which pages to scan - SCAN ALL PAGES to prevent missed items"""
        
        # For militaria321.com end-date sorting issue, we need to scan ALL pages
        # to ensure no new items are missed regardless of their position
        
        if total_pages_estimate and total_pages_estimate > 0:
            # Scan all pages up to the estimate, respecting max limit
            max_pages_to_scan = min(total_pages_estimate, max_pages_per_cycle)
            return list(range(1, max_pages_to_scan + 1))
        else:
            # No estimate available: scan up to max limit to be safe
            return list(range(1, max_pages_per_cycle + 1))
    
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

    def _apply_posted_ts_fallback(self, items: List[Listing]) -> None:
        if not items:
            return

        fallback_ts = get_utc_now() - timedelta(days=1)
        for item in items:
            if item.platform == "egun.de" and item.posted_ts is None:
                item.posted_ts = fallback_ts
            self._normalize_listing_timestamp(item)

    def _normalize_since_ts(self, keyword: Keyword) -> datetime:
        """Ensure keyword.since_ts is an aware UTC datetime."""

        since_ts = to_utc_aware(keyword.since_ts)
        if since_ts is None:
            since_ts = get_utc_now()
        keyword.since_ts = since_ts
        return since_ts

    @staticmethod
    def _normalize_listing_timestamp(item: Listing) -> Optional[datetime]:
        """Normalize listing.posted_ts to UTC-aware datetime."""

        posted_ts = to_utc_aware(item.posted_ts)
        item.posted_ts = posted_ts
        return posted_ts

    def _log_tz_compare(
        self,
        keyword: Keyword,
        original_since_ts: Optional[datetime],
        original_posted_ts: Optional[datetime],
        since_ts_utc: datetime,
        posted_ts_utc: Optional[datetime],
    ) -> None:
        """Emit structured timezone comparison telemetry."""

        since_kind = "aware" if original_since_ts and original_since_ts.tzinfo else "naive"
        posted_kind = "aware" if original_posted_ts and original_posted_ts.tzinfo else "naive"

        logger.info(
            {
                "event": "tz_compare",
                "keyword": keyword.normalized_keyword,
                "since_ts_kind": since_kind,
                "posted_ts_kind": posted_kind,
                "posted_ts_present": posted_ts_utc is not None,
                "since_ts_utc": since_ts_utc.isoformat(),
                "posted_ts_utc": posted_ts_utc.isoformat() if posted_ts_utc else None,
            }
        )

    async def _handle_provider_metadata(
        self,
        platform_name: str,
        metadata: Optional[dict],
        keyword: Optional[Keyword] = None,
    ) -> None:
        metadata = metadata or {}
        self.provider_status[platform_name] = metadata
        events = metadata.get("events") or []
        if not events:
            return

        if not self.notification_service:
            return

        for event in events:
            event_name = event.get("event")
            if event_name == "captcha_detected":
                try:
                    await self.notification_service.send_admin_event(event)
                except Exception as exc:
                    logger.error(
                        {
                            "event": "admin_notify_failed",
                            "platform": platform_name,
                            "error": str(exc),
                        }
                    )
            elif event_name == "ka_recaptcha" and keyword is not None:
                try:
                    user = await self.db.get_user_by_id(keyword.user_id)
                    if user:
                        await self.notification_service.send_recaptcha_warning(
                            user.telegram_id, keyword, event
                        )
                except Exception as exc:
                    logger.error(
                        {
                            "event": "recaptcha_notify_failed",
                            "platform": platform_name,
                            "keyword": keyword.normalized_keyword,
                            "error": str(exc),
                        }
                    )

    async def _ensure_keyword_platforms(self, keyword: Keyword) -> List[str]:
        expected_order = list(self.providers.keys())
        current = list(keyword.platforms or [])
        filtered = [p for p in current if p in expected_order]

        for provider_name in expected_order:
            if provider_name not in filtered:
                filtered.append(provider_name)

        if filtered != current:
            keyword.platforms = filtered
            await self.db.db.keywords.update_one(
                {"id": keyword.id},
                {"$set": {"platforms": filtered}}
            )
            logger.info(
                {
                    "event": "platforms_sync",
                    "keyword": keyword.normalized_keyword,
                    "platforms": filtered,
                }
            )

        return filtered

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
                "updated_at": get_utc_now()
            }}
        )
    
    def compute_keyword_health(self, keyword: Keyword, now_utc: datetime, scheduler) -> tuple[str, str]:
        """Compute keyword health status and reason"""
        from zoneinfo import ZoneInfo
        
        def berlin(dt_utc: datetime | None) -> str:
            if not dt_utc:
                return "/"
            return dt_utc.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M") + " Uhr"
        
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
        last_success_utc = to_utc_aware(keyword.last_success_ts)
        last_error_utc = to_utc_aware(keyword.last_error_ts)

        if last_success_utc is None and last_error_utc is not None:
            reason = "Noch kein erfolgreicher Lauf"
            if keyword.last_error_message:
                reason += f": {keyword.last_error_message[:100]}"
            return "❌ Fehler", reason

        # Rule 5: Stale success
        if last_success_utc and (now_utc - last_success_utc).total_seconds() > STALE_WARN_SEC:
            age_seconds = (now_utc - last_success_utc).total_seconds()
            if age_seconds < 3600:  # Less than 1 hour
                age = f"{int(age_seconds // 60)} Min"
            else:  # Hours
                age = f"{int(age_seconds // 3600)} Std"
            return "⚠️ Warnung", f"Zu lange kein Erfolg: letzte Prüfung vor {age}"
        
        # Rule 6: Healthy
        return "✅ Läuft", "Letzte Prüfung erfolgreich"
    
    async def diagnose_keyword(self, keyword: Keyword, scheduler) -> str:
        """Comprehensive keyword diagnosis with German output"""
        
        diagnosis_lines = [f"🔍 {b('Diagnose für')} {keyword.original_keyword}", ""]
        
        # 1. Baseline state analysis
        baseline_parts = [f"Baseline: {keyword.baseline_status}"]
        
        if keyword.baseline_pages_scanned:
            total_pages = sum(keyword.baseline_pages_scanned.values())
            baseline_parts.append(f"Seiten: {total_pages}")
        
        if keyword.baseline_items_collected:
            total_items = sum(keyword.baseline_items_collected.values())
            baseline_parts.append(f"Items: {total_items}")
        
        if keyword.baseline_errors:
            first_error = next(iter(keyword.baseline_errors.values()))
            baseline_parts.append(f"Fehler: {first_error[:50]}")
        else:
            baseline_parts.append("Fehler: /")
        
        diagnosis_lines.append(f"• {' — '.join(baseline_parts)}")
        
        # 2. Scheduler analysis
        job_id = f"keyword_{keyword.id}"
        has_job = scheduler.scheduler_has_job(job_id)
        
        if has_job:
            next_run = scheduler.get_job_next_run(job_id)
            next_run_str = fmt_ts_de(next_run) if next_run else "unbekannt"
            scheduler_info = f"• Scheduler: vorhanden — Nächster Lauf: {next_run_str}"
        else:
            scheduler_info = "• Scheduler: ❌ FEHLT"
        
        diagnosis_lines.append(scheduler_info)
        diagnosis_lines.append("")
        
        # 3. Provider dry-run probe
        provider_results = {}
        
        for platform_name, provider in self.providers.items():
            try:
                # Probe first page only for diagnosis
                result = await provider.search(
                    keyword=keyword.original_keyword,
                    crawl_all=False  # Just first page for probe
                )

                await self._handle_provider_metadata(platform_name, result.metadata, keyword)

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
        diagnosis_lines.append("")
        
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
        
        return br_join(diagnosis_lines)
    
    async def reseed_seen_keys_for_keyword(self, keyword_id: str) -> dict:
        """Migration: Re-crawl and populate ID-based seen_listing_keys for a keyword
        
        This is used to migrate keywords with empty or title-based seen_listing_keys
        to the new ID-based system without sending notifications.
        """
        logger.info(f"Starting seen_listing_keys reseed for keyword {keyword_id}")
        
        # Get the keyword
        doc = await self.db.db.keywords.find_one({"id": keyword_id})
        if not doc:
            raise ValueError(f"Keyword {keyword_id} not found")
        
        from models import Keyword
        keyword = Keyword(**doc)
        
        # Mark as reseeding
        await self.db.db.keywords.update_one(
            {"id": keyword_id},
            {"$set": {
                "baseline_status": "running",
                "baseline_started_ts": get_utc_now(),
                "baseline_errors": {}
            }}
        )
        
        try:
            # Crawl all pages to collect ID-based listing_keys
            all_listing_keys = []
            provider_results = {}
            
            for platform_name, provider in self.providers.items():
                try:
                    logger.info(f"Reseeding {platform_name} for keyword '{keyword.original_keyword}'")
                    
                    result = await provider.search(
                        keyword=keyword.original_keyword,
                        crawl_all=True  # Full crawl for reseed
                    )

                    # Extract listing keys (no detail fetches during reseed)
                    for item in result.items:
                        self._normalize_listing_timestamp(item)
                        listing_key = self._build_canonical_listing_key(item)
                        all_listing_keys.append(listing_key)
                    
                    provider_results[platform_name] = {
                        "pages_scanned": result.pages_scanned or 0,
                        "items_collected": len(result.items)
                    }
                    
                except Exception as e:
                    error_msg = str(e)[:400]
                    provider_results[platform_name] = {"error": error_msg}
                    logger.error(f"Error reseeding {platform_name}: {error_msg}")
            
            # Remove duplicates
            unique_listing_keys = list(set(all_listing_keys))
            
            # Update database atomically
            await self.db.db.keywords.update_one(
                {"id": keyword_id},
                {"$set": {
                    "seen_listing_keys": unique_listing_keys,
                    "baseline_status": "complete",
                    "baseline_completed_ts": get_utc_now(),
                    "baseline_pages_scanned": {p: r.get("pages_scanned", 0) for p, r in provider_results.items() if "error" not in r},
                    "baseline_items_collected": {p: r.get("items_collected", 0) for p, r in provider_results.items() if "error" not in r},
                    "baseline_errors": {p: r["error"] for p, r in provider_results.items() if "error" in r},
                    "updated_at": get_utc_now()
                }}
            )
            
            logger.info(f"Reseed completed: {len(unique_listing_keys)} unique listing keys")
            
            return {
                "success": True,
                "unique_keys": len(unique_listing_keys),
                "provider_results": provider_results
            }
            
        except Exception as e:
            # Mark as error
            await self.db.db.keywords.update_one(
                {"id": keyword_id},
                {"$set": {
                    "baseline_status": "error",
                    "baseline_errors": {"reseed": str(e)[:400]},
                    "updated_at": get_utc_now()
                }}
            )
            raise

    async def full_baseline_seed(self, keyword_text: str, keyword_id: str) -> tuple[List[Listing], dict]:
        """Perform full baseline crawl with proper state machine
        
        Implements baseline_status transitions: pending → running → complete/partial/error
        Returns: (all_items, last_item_meta)
        """
        now_utc = get_utc_now()
        
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

                await self._handle_provider_metadata(
                    platform_name, result.metadata
                )

                for item in result.items:
                    self._normalize_listing_timestamp(item)

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
        now_utc = get_utc_now()
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
    
    async def manual_backfill_check(self, keyword_text: str, user_id: str) -> dict:
        """Manual verification crawl for /check covering all providers."""

        normalized_keyword = self.normalize_keyword(keyword_text)
        keyword = await self.db.get_keyword_by_normalized(user_id, normalized_keyword, active_only=True)

        if not keyword:
            return {
                "error": f"Keine aktive Überwachung für '{keyword_text}' gefunden.",
                "providers": {},
                "backfill": {
                    "unprocessed": 0,
                    "new_notifications": 0,
                    "already_known": 0,
                },
            }

        enabled_platforms = set(await self._ensure_keyword_platforms(keyword))

        provider_reports: dict[str, dict] = {}
        all_items: List[Listing] = []
        seen_this_run: set[str] = set()
        per_platform_unseen: dict[str, List[Listing]] = {}
        per_platform_stats: dict[str, dict[str, int]] = {}

        since_ts_original = keyword.since_ts
        since_ts_utc = self._normalize_since_ts(keyword)

        for platform_name, provider in self.providers.items():
            if platform_name not in enabled_platforms:
                provider_reports[platform_name] = {
                    "platform": platform_name,
                    "enabled": False,
                    "pages": 0,
                    "items": 0,
                    "errors": "deaktiviert",
                    "metadata": self.provider_status.get(platform_name, {}),
                }
                logger.info(
                    {
                        "event": "manual_check",
                        "keyword": keyword.normalized_keyword,
                        "platform": platform_name,
                        "pages": 0,
                        "items": 0,
                        "errors": "deaktiviert",
                    }
                )
                continue

            try:
                logger.info(
                    {
                        "event": "manual_check_start",
                        "keyword": keyword.normalized_keyword,
                        "platform": platform_name,
                    }
                )

                result = await provider.search(
                    keyword=keyword.original_keyword,
                    crawl_all=True,
                    max_pages_override=MAX_PAGES_PER_CYCLE,
                )

                await self._handle_provider_metadata(
                    platform_name, result.metadata, keyword
                )
                metadata = result.metadata or {}

                provider_reports[platform_name] = {
                    "platform": platform_name,
                    "enabled": True,
                    "pages": result.pages_scanned or 0,
                    "items": len(result.items),
                    "errors": None,
                    "metadata": metadata,
                    "last_error": metadata.get("last_error"),
                }

                for item in result.items:
                    listing_key = self._build_canonical_listing_key(item)

                    if listing_key in seen_this_run:
                        continue
                    seen_this_run.add(listing_key)

                    item.platform_id = listing_key.split(":", 1)[1]
                    original_posted_ts = item.posted_ts
                    posted_ts_utc = self._normalize_listing_timestamp(item)
                    self._log_tz_compare(
                        keyword,
                        since_ts_original,
                        original_posted_ts,
                        since_ts_utc,
                        posted_ts_utc,
                    )
                    all_items.append(item)

                    stats = per_platform_stats.setdefault(
                        platform_name, {"unseen": 0, "already_known": 0, "pushed": 0}
                    )

                    if listing_key not in keyword.seen_listing_keys:
                        stats["unseen"] += 1
                        per_platform_unseen.setdefault(platform_name, []).append(item)
                    else:
                        stats["already_known"] += 1

            except Exception as exc:
                error_msg = str(exc)[:400]
                provider_reports[platform_name] = {
                    "platform": platform_name,
                    "enabled": True,
                    "pages": 0,
                    "items": 0,
                    "errors": error_msg,
                    "metadata": self.provider_status.get(platform_name, {}),
                }
                logger.error(f"Error during manual check for {platform_name}: {error_msg}")

            logger.info(
                {
                    "event": "manual_check",
                    "keyword": keyword.normalized_keyword,
                    "platform": platform_name,
                    "pages": provider_reports[platform_name]["pages"],
                    "items": provider_reports[platform_name]["items"],
                    "errors": provider_reports[platform_name]["errors"],
                }
            )

            logger.info(
                {
                    "event": "provider_summary",
                    "platform": platform_name,
                    "q": keyword.normalized_keyword,
                    "pages": provider_reports[platform_name]["pages"],
                    "items": provider_reports[platform_name]["items"],
                    "errors": provider_reports[platform_name]["errors"],
                }
            )

        total_unseen = sum(stats.get("unseen", 0) for stats in per_platform_stats.values())
        already_known_total = sum(stats.get("already_known", 0) for stats in per_platform_stats.values())

        logger.info(
            {
                "event": "manual_check_summary",
                "keyword": keyword.normalized_keyword,
                "backfill_candidates": total_unseen,
                "already_known": already_known_total,
            }
        )

        for platform_name, items in per_platform_unseen.items():
            provider = self.providers.get(platform_name)
            if provider and hasattr(provider, "fetch_posted_ts_batch"):
                try:
                    await provider.fetch_posted_ts_batch(items, concurrency=DETAIL_CONCURRENCY)
                except Exception as exc:
                    logger.warning(f"Detail fetch failed for {platform_name}: {exc}")
            self._apply_posted_ts_fallback(items)

        self._apply_posted_ts_fallback(all_items)

        new_seen_keys: List[str] = []
        absorbed_count = 0
        notifications_by_platform: dict[str, List[Listing]] = {}

        for platform_name, items in per_platform_unseen.items():
            for item in items:
                listing_key = self._build_canonical_listing_key(item)
                new_seen_keys.append(listing_key)

                if self._is_new_listing(item, keyword):
                    notifications_by_platform.setdefault(platform_name, []).append(item)
                    logger.info(
                        {
                            "event": "backfill_decision",
                            "platform": item.platform,
                            "keyword_norm": keyword.normalized_keyword,
                            "listing_key": listing_key,
                            "posted_ts_utc": item.posted_ts.isoformat() if item.posted_ts else None,
                            "since_ts_utc": keyword.since_ts.isoformat(),
                            "decision": "backfill_push",
                            "reason": "posted_ts>=since_ts" if item.posted_ts else "grace_window_allowed",
                        }
                    )
                else:
                    absorbed_count += 1
                    reason = self._get_filter_reason(item, keyword)
                    logger.info(
                        {
                            "event": "backfill_decision",
                            "platform": item.platform,
                            "keyword_norm": keyword.normalized_keyword,
                            "listing_key": listing_key,
                            "posted_ts_utc": item.posted_ts.isoformat() if item.posted_ts else None,
                            "since_ts_utc": keyword.since_ts.isoformat(),
                            "decision": "backfill_absorb",
                            "reason": reason,
                        }
                    )

        if new_seen_keys:
            await self.db.db.keywords.update_one(
                {"id": keyword.id},
                {"$addToSet": {"seen_listing_keys": {"$each": new_seen_keys}}},
            )
            logger.info(
                {
                    "event": "backfill_seen_update",
                    "keyword": keyword.normalized_keyword,
                    "added": len(new_seen_keys),
                }
            )

        actual_pushed = 0
        if notifications_by_platform:
            from simple_bot import notification_service as global_notification_service

            for platform_name, items in notifications_by_platform.items():
                for item in items:
                    try:
                        user = await self.db.get_user_by_id(user_id)
                        if user:
                            success = await global_notification_service.send_new_item_notification(
                                user.telegram_id, keyword, item
                            )
                            if success:
                                actual_pushed += 1
                                stats = per_platform_stats.setdefault(
                                    platform_name, {"unseen": 0, "already_known": 0, "pushed": 0}
                                )
                                stats["pushed"] += 1
                    except Exception as exc:
                        listing_key = self._build_canonical_listing_key(item)
                        logger.error(f"Failed to send backfill notification for {listing_key}: {exc}")

        for platform_name, report in provider_reports.items():
            metadata = report.get("metadata") or self.provider_status.get(platform_name, {})
            stats = per_platform_stats.get(platform_name, {"unseen": 0, "already_known": 0, "pushed": 0})
            report["metadata"] = metadata
            report["unseen_candidates"] = stats.get("unseen", 0)
            report["already_known"] = stats.get("already_known", 0)
            report["pushed"] = stats.get("pushed", 0)
            report.setdefault("last_error", metadata.get("last_error"))
            report["since_ts"] = fmt_ts_de(keyword.since_ts)
            report["cooldown_active"] = metadata.get("cooldown_active") if metadata else None
            report["cooldown_until"] = metadata.get("cooldown_until") if metadata else None

        for item in all_items:
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
                end_ts=item.end_ts,
            )
            await self.db.upsert_listing(stored_listing)

        return {
            "error": None,
            "providers": provider_reports,
            "backfill": {
                "unprocessed": total_unseen,
                "new_notifications": actual_pushed,
                "already_known": already_known_total + absorbed_count,
            },
        }
    
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
        since_ts_utc = self._normalize_since_ts(keyword)
        posted_ts_utc = self._normalize_listing_timestamp(item)

        if posted_ts_utc is not None:
            # Has posted_ts: must be >= since_ts
            return posted_ts_utc >= since_ts_utc
        else:
            # No posted_ts: allow within 60-minute grace window
            grace_window = timedelta(minutes=60)
            now_utc_value = get_utc_now()
            time_since_subscription = now_utc_value - since_ts_utc
            return time_since_subscription <= grace_window
    
    def _get_filter_reason(self, item: Listing, keyword: Keyword) -> str:
        """Get reason why item was filtered out"""
        listing_key = f"{item.platform}:{item.platform_id}"
        
        if listing_key in keyword.seen_listing_keys:
            return "already_seen"
        
        since_ts_utc = self._normalize_since_ts(keyword)
        posted_ts_utc = self._normalize_listing_timestamp(item)

        if posted_ts_utc is not None:
            if posted_ts_utc < since_ts_utc:
                return "posted_ts<since_ts"
        else:
            grace_window = timedelta(minutes=60)
            now_utc_value = get_utc_now()
            time_since_subscription = now_utc_value - since_ts_utc
            if time_since_subscription > grace_window:
                return "no_posted_ts_beyond_grace"
        
        return "unknown"
    
    @staticmethod
    def normalize_keyword(keyword: str) -> str:
        """Normalize keyword with Unicode NFKC as specified"""
        return unicodedata.normalize('NFKC', keyword.strip().lower())
