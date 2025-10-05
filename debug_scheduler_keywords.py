#!/usr/bin/env python3
"""
Debug what keywords the scheduler thinks it has
"""

import asyncio
import sys

# Add the article_hunter_bot directory to Python path
sys.path.insert(0, '/app/article_hunter_bot')

from database import DatabaseManager
from scheduler import PollingScheduler

async def debug_scheduler():
    """Debug scheduler state"""
    
    print("=== Debugging Scheduler State ===\n")
    
    # Initialize database
    db_manager = DatabaseManager() 
    await db_manager.initialize()
    
    # Initialize scheduler (but don't start it)
    scheduler = PollingScheduler(None, None)  # We won't start it, just check state
    
    # Check what jobs are in the scheduler
    print("Active APScheduler jobs:")
    for job in scheduler.scheduler.get_jobs():
        print(f"  Job ID: {job.id}")
        print(f"    Name: {job.name}")
        print(f"    Next run: {job.next_run_time}")
        print(f"    Args: {job.args}")
        print()
    
    # Check database keywords vs scheduler jobs
    cursor = db_manager.db.keywords.find({"is_active": True})
    keyword_docs = await cursor.to_list(length=None)
    
    from models import Keyword
    db_keywords = [Keyword(**doc) for doc in keyword_docs]
    
    print(f"Database keywords: {len(db_keywords)}")
    for kw in db_keywords:
        job_id = f"keyword_{kw.id}"
        has_job = scheduler.scheduler.get_job(job_id) is not None
        print(f"  {kw.normalized_keyword} (ID: {kw.id[:8]}...) - Job exists: {has_job}")
        print(f"    Since: {kw.since_ts}")
        print(f"    Seen keys: {len(kw.seen_listing_keys)}")
    
    await db_manager.close()

if __name__ == "__main__":
    asyncio.run(debug_scheduler())