import asyncio
import logging
from datetime import datetime
from typing import Dict, Set, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from database import DatabaseManager
from models import User, Keyword
from services.search_service import SearchService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class PollingScheduler:
    """APScheduler for fixed 60-second keyword polling"""
    
    def __init__(
        self, 
        db_manager: DatabaseManager, 
        search_service: Optional[SearchService] = None,
        notification_service: Optional[NotificationService] = None
    ):
        self.db = db_manager
        self.search_service = search_service
        self.notification_service = notification_service
        self.scheduler = AsyncIOScheduler()
        self.active_jobs: Set[str] = set()  # Track active keyword IDs
        
    async def start(self):
        """Start the scheduler and set up existing keywords"""
        self.scheduler.start()
        
        # Set up jobs for existing active keywords
        await self._setup_existing_keywords()
        
        logger.info("Polling scheduler started")
    
    async def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown(wait=True)
        logger.info("Polling scheduler stopped")
    
    async def _setup_existing_keywords(self):
        """Set up polling jobs for existing active keywords"""
        try:
            logger.info("Setting up existing keyword jobs...")
            
            # Get all users with active keywords
            users_cursor = self.db.db.users.find({})
            jobs_added = 0
            
            async for user in users_cursor:
                # Get active keywords for this user
                keywords_cursor = self.db.db.keywords.find({
                    'user_id': user['id'], 
                    'is_active': True
                })
                
                async for keyword_doc in keywords_cursor:
                    # Convert to Keyword object
                    from models import Keyword
                    keyword = Keyword(**keyword_doc)
                    
                    # Add polling job
                    self.add_keyword_job(keyword, user['telegram_id'])
                    jobs_added += 1
            
            logger.info(f"Restored {jobs_added} keyword polling jobs")
            
        except Exception as e:
            logger.error(f"Error setting up existing keywords: {e}")
    
    def add_keyword_job(self, keyword: Keyword, user_telegram_id: int):
        """Add polling job for a keyword with fixed 60-second interval"""
        if keyword.id in self.active_jobs:
            return  # Already monitoring
        
        job_id = f"keyword_{keyword.id}"
        
        # Fixed 60-second polling as specified with proper job control
        self.scheduler.add_job(
            func=self._poll_keyword,
            trigger=IntervalTrigger(seconds=60),
            args=[keyword.id, user_telegram_id],
            id=job_id,
            name=f"Poll keyword: {keyword.original_keyword}",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping runs
            coalesce=True     # Merge pending executions
        )
        
        self.active_jobs.add(keyword.id)
        logger.info(f"Added polling job for keyword '{keyword.original_keyword}' (60s interval)")
    
    def remove_keyword_job(self, keyword_id: str):
        """Remove polling job for a keyword"""
        if keyword_id not in self.active_jobs:
            return  # Not monitoring
        
        job_id = f"keyword_{keyword_id}"
        
        try:
            self.scheduler.remove_job(job_id)
            self.active_jobs.discard(keyword_id)
            logger.info(f"Removed polling job for keyword ID {keyword_id}")
        except Exception as e:
            logger.warning(f"Error removing job {job_id}: {e}")
    
    async def _poll_keyword(self, keyword_id: str, user_telegram_id: int):
        """Poll a single keyword for new items"""
        try:
            # Get user by telegram ID first
            user = await self.db.get_user_by_telegram_id(user_telegram_id)
            if not user:
                self.remove_keyword_job(keyword_id)
                return
            
            # Get current keyword state
            keywords = await self.db.get_user_keywords(user.id, active_only=True)
            keyword = None
            for kw in keywords:
                if kw.id == keyword_id:
                    keyword = kw
                    break
            
            if not keyword or not keyword.is_active:
                # Keyword deleted or deactivated, remove job
                self.remove_keyword_job(keyword_id)
                return
            
            # Search for new items
            new_items = await self.search_service.search_keyword(keyword)
            
            # Counters for logging
            checked_count = len(new_items)
            pushed_count = 0
            skipped_seen = 0
            skipped_old = 0
            skipped_duplicate = 0
            
            # Collect all seen keys (pushed + absorbed)
            new_seen_keys = []
            
            # Send notifications for new items
            for item in new_items:
                try:
                    was_sent = await self.notification_service.send_new_item_notification(
                        user_telegram_id, keyword, item
                    )
                    if was_sent:
                        pushed_count += 1
                    else:
                        skipped_duplicate += 1
                    
                    # Always add to seen keys (whether pushed or duplicate)
                    listing_key = self._build_canonical_listing_key(item)
                    new_seen_keys.append(listing_key)
                        
                except Exception as e:
                    logger.error(f"Error sending notification for {item.platform}:{item.platform_id}: {e}")
            
            # Update keyword's seen keys if we have new keys to add
            if new_seen_keys:
                # Add new keys to existing seen keys (avoid duplicates)
                updated_seen_keys = list(set(keyword.seen_listing_keys + new_seen_keys))
                await self.db.update_keyword_seen_keys(keyword.id, updated_seen_keys)
            
            # Log poll summary
            logger.info({
                "event": "poll_summary",
                "keyword": keyword.original_keyword,
                "checked": checked_count,
                "pushed": pushed_count,
                "skipped_seen": skipped_seen,
                "skipped_old": skipped_old,
                "skipped_duplicate": skipped_duplicate
            })
            
        except Exception as e:
            logger.error(f"Error polling keyword {keyword_id}: {e}")
    
    def _build_canonical_listing_key(self, item) -> str:
        """Build canonical listing key: militaria321.com:<numeric_id>"""
        # Ensure platform is lowercase and normalized
        platform = item.platform.lower().strip()
        
        # Extract numeric ID if platform_id contains extra data
        import re
        numeric_id = re.search(r'(\d+)', item.platform_id)
        if numeric_id:
            clean_id = numeric_id.group(1)
        else:
            clean_id = item.platform_id
        
        return f"{platform}:{clean_id}"
    
    def scheduler_has_job(self, job_id: str) -> bool:
        """Check if scheduler has a job with given ID"""
        return self.scheduler.get_job(job_id) is not None
    
    def get_job_next_run(self, job_id: str) -> Optional[datetime]:
        """Get next run time for job (UTC)"""
        job = self.scheduler.get_job(job_id)
        return job.next_run_time if job else None
