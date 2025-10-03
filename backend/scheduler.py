import asyncio
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from database import DatabaseManager
from services.search_service import SearchService

logger = logging.getLogger(__name__)


class JobScheduler:
    """Job scheduler for auction monitoring"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.scheduler = AsyncIOScheduler()
        self.search_service = SearchService(db_manager)
        self.is_running = False
    
    async def start(self):
        """Start the scheduler"""
        if self.is_running:
            logger.warning("Scheduler already running")
            return
        
        try:
            # Initialize database
            await self.db.initialize()
            
            # Add jobs
            await self._add_jobs()
            
            # Start scheduler
            self.scheduler.start()
            self.is_running = True
            
            logger.info("Job scheduler started successfully")
            
        except Exception as e:
            logger.error(f"Error starting scheduler: {e}")
            raise
    
    async def stop(self):
        """Stop the scheduler"""
        if not self.is_running:
            return
        
        try:
            self.scheduler.shutdown()
            await self.db.close()
            self.is_running = False
            logger.info("Job scheduler stopped")
        except Exception as e:
            logger.error(f"Error stopping scheduler: {e}")
    
    async def _add_jobs(self):
        """Add scheduled jobs"""
        
        # Main search job - runs every minute
        self.scheduler.add_job(
            func=self._search_job,
            trigger=IntervalTrigger(seconds=60),
            id="main_search",
            name="Main Search Job",
            replace_existing=True,
            max_instances=1,
            coalesce=True
        )
        
        # Cleanup job - runs daily at 2 AM
        self.scheduler.add_job(
            func=self._cleanup_job,
            trigger=CronTrigger(hour=2, minute=0),
            id="daily_cleanup",
            name="Daily Cleanup Job",
            replace_existing=True,
            max_instances=1
        )
        
        # Health check job - runs every 5 minutes
        self.scheduler.add_job(
            func=self._health_check_job,
            trigger=IntervalTrigger(minutes=5),
            id="health_check",
            name="Health Check Job",
            replace_existing=True,
            max_instances=1
        )
        
        logger.info("Scheduled jobs added")
    
    async def _search_job(self):
        """Main search job - searches all active keywords"""
        try:
            logger.debug("Starting search job")
            
            # Get current time
            now = datetime.utcnow()
            
            # Get all active keywords that need checking
            keywords = await self.db.get_all_active_keywords()
            
            # Filter keywords by frequency
            keywords_to_check = []
            for keyword in keywords:
                # Calculate if keyword needs checking based on frequency
                if self._should_check_keyword(keyword, now):
                    keywords_to_check.append(keyword)
            
            if keywords_to_check:
                logger.info(f"Checking {len(keywords_to_check)} keywords")
                
                # Process keywords in batches
                batch_size = 3
                for i in range(0, len(keywords_to_check), batch_size):
                    batch = keywords_to_check[i:i + batch_size]
                    
                    # Process batch concurrently
                    tasks = []
                    for keyword in batch:
                        task = self.search_service.search_keyword(keyword)
                        tasks.append(task)
                    
                    # Wait for batch to complete
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # Log results
                    for keyword, result in zip(batch, results):
                        if isinstance(result, Exception):
                            logger.error(f"Exception processing keyword '{keyword.keyword}': {result}")
                        else:
                            new_listings = result.get("new_listings", 0)
                            if new_listings > 0:
                                logger.info(f"Found {new_listings} new listings for '{keyword.keyword}'")
                    
                    # Small delay between batches to be respectful
                    if i + batch_size < len(keywords_to_check):
                        await asyncio.sleep(1)
            
            logger.debug("Search job completed")
            
        except Exception as e:
            logger.error(f"Error in search job: {e}")
    
    def _should_check_keyword(self, keyword, now: datetime) -> bool:
        """Determine if keyword should be checked based on frequency"""
        if not keyword.last_checked:
            return True
        
        # Check if muted
        if keyword.is_muted:
            if keyword.muted_until and now < keyword.muted_until:
                return False
            elif keyword.muted_until and now >= keyword.muted_until:
                # Unmute keyword
                asyncio.create_task(self.db.update_keyword(keyword.id, {
                    "is_muted": False,
                    "muted_until": None
                }))
        
        # Calculate next check time
        next_check = keyword.last_checked + timedelta(seconds=keyword.frequency_seconds)
        return now >= next_check
    
    async def _cleanup_job(self):
        """Daily cleanup job"""
        try:
            logger.info("Starting cleanup job")
            
            now = datetime.utcnow()
            
            # Clean up old notifications (keep 30 days)
            cutoff_date = now - timedelta(days=30)
            
            result = await self.db.db.notifications.delete_many({
                "sent_at": {"$lt": cutoff_date}
            })
            
            if result.deleted_count > 0:
                logger.info(f"Cleaned up {result.deleted_count} old notifications")
            
            # Clean up old keyword hits (keep 90 days)
            cutoff_date = now - timedelta(days=90)
            
            result = await self.db.db.keyword_hits.delete_many({
                "seen_ts": {"$lt": cutoff_date}
            })
            
            if result.deleted_count > 0:
                logger.info(f"Cleaned up {result.deleted_count} old keyword hits")
            
            logger.info("Cleanup job completed")
            
        except Exception as e:
            logger.error(f"Error in cleanup job: {e}")
    
    async def _health_check_job(self):
        """Health check job"""
        try:
            logger.debug("Running health check")
            
            # Check database connection
            await self.db.db.command("ping")
            
            # Check active keywords count
            keywords_count = await self.db.db.keywords.count_documents({"is_active": True})
            
            # Check recent activity
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            recent_hits = await self.db.db.keyword_hits.count_documents({
                "seen_ts": {"$gte": one_hour_ago}
            })
            
            logger.debug(f"Health check: {keywords_count} active keywords, {recent_hits} recent hits")
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
    
    def get_job_stats(self):
        """Get scheduler statistics"""
        if not self.is_running:
            return {"status": "stopped"}
        
        jobs = []
        for job in self.scheduler.get_jobs():
            job_info = {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            }
            jobs.append(job_info)
        
        return {
            "status": "running",
            "jobs": jobs,
            "job_count": len(jobs)
        }


# Global scheduler instance
job_scheduler = None