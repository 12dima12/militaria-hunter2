from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

# Import project modules
from database import DatabaseManager
from telegram_bot import TelegramBotManager
from scheduler import JobScheduler
from models import User, Keyword

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
from dotenv import load_dotenv
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Global instances
db_manager = None
telegram_bot_manager = None
job_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global db_manager, telegram_bot_manager, job_scheduler
    
    logger.info("Starting Militaria Auction Bot application...")
    
    try:
        # Initialize database
        db_manager = DatabaseManager()
        await db_manager.initialize()
        logger.info("Database initialized")
        
        # Initialize job scheduler
        job_scheduler = JobScheduler(db_manager)
        
        # Start scheduler in background
        scheduler_task = asyncio.create_task(job_scheduler.start())
        logger.info("Job scheduler started")
        
        # Initialize Telegram bot
        telegram_bot_manager = TelegramBotManager(db_manager)
        
        # Start bot in background
        bot_task = asyncio.create_task(telegram_bot_manager.start())
        logger.info("Telegram bot started")
        
        logger.info("🎖️ Militaria Auction Bot is running!")
        
        yield
        
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise
    finally:
        # Cleanup
        logger.info("Shutting down application...")
        
        if job_scheduler:
            await job_scheduler.stop()
        
        if telegram_bot_manager:
            await telegram_bot_manager.stop()
        
        if db_manager:
            await db_manager.close()
        
        logger.info("Application shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Militaria Auction Bot API",
    description="Telegram bot for monitoring auction platforms",
    version="1.0.0",
    lifespan=lifespan
)

# Create API router
api_router = APIRouter(prefix="/api")


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@api_router.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Militaria Auction Bot API",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat()
    }


@api_router.get("/health")
async def health_check():
    """Health check endpoint"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {}
    }
    
    try:
        # Check database
        if db_manager:
            await db_manager.db.command("ping")
            health_status["components"]["database"] = "healthy"
        else:
            health_status["components"]["database"] = "not_initialized"
        
        # Check bot
        if telegram_bot_manager and telegram_bot_manager.is_running:
            bot_info = await telegram_bot_manager.get_bot_info()
            if bot_info:
                health_status["components"]["telegram_bot"] = "healthy"
                health_status["bot_info"] = bot_info
            else:
                health_status["components"]["telegram_bot"] = "error"
        else:
            health_status["components"]["telegram_bot"] = "not_running"
        
        # Check scheduler
        if job_scheduler and job_scheduler.is_running:
            health_status["components"]["scheduler"] = "healthy"
            health_status["scheduler_jobs"] = job_scheduler.get_job_stats()
        else:
            health_status["components"]["scheduler"] = "not_running"
        
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["error"] = str(e)
        logger.error(f"Health check failed: {e}")
    
    return health_status


@api_router.get("/stats")
async def get_stats():
    """Get application statistics"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Get various statistics
        stats = {
            "timestamp": datetime.utcnow().isoformat(),
            "users_count": await db_manager.db.users.count_documents({}),
            "active_keywords": await db_manager.db.keywords.count_documents({"is_active": True}),
            "total_keywords": await db_manager.db.keywords.count_documents({}),
            "total_listings": await db_manager.db.listings.count_documents({}),
            "total_hits": await db_manager.db.keyword_hits.count_documents({}),
            "notifications_sent": await db_manager.db.notifications.count_documents({"status": "sent"})
        }
        
        # Recent activity (last 24 hours)
        from datetime import timedelta
        yesterday = datetime.utcnow() - timedelta(days=1)
        
        stats["recent_activity"] = {
            "new_users_24h": await db_manager.db.users.count_documents({
                "created_at": {"$gte": yesterday}
            }),
            "new_hits_24h": await db_manager.db.keyword_hits.count_documents({
                "seen_ts": {"$gte": yesterday}
            }),
            "notifications_24h": await db_manager.db.notifications.count_documents({
                "sent_at": {"$gte": yesterday}
            })
        }
        
        return stats
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving statistics")


@api_router.post("/admin/search-test")
async def test_search(keyword: str = "Wehrmacht"):
    """Test search functionality (admin endpoint)"""
    if not job_scheduler:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")
    
    try:
        # Create a test search
        from providers.militaria321 import Militaria321Provider
        
        provider = Militaria321Provider()
        results = await provider.search(keyword)
        
        return {
            "keyword": keyword,
            "platform": "militaria321.com",
            "results_count": len(results),
            "results": [
                {
                    "title": r.title,
                    "url": r.url,
                    "price": f"{r.price_value} {r.price_currency}" if r.price_value else None,
                    "platform_id": r.platform_id
                }
                for r in results[:5]  # Return first 5 results
            ]
        }
        
    except Exception as e:
        logger.error(f"Error in test search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/admin/trigger-search")
async def trigger_manual_search():
    """Trigger manual search of all active keywords (admin endpoint)"""
    if not job_scheduler:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")
    
    try:
        # Trigger search job manually
        result = await job_scheduler.search_service.search_all_active_keywords()
        return result
        
    except Exception as e:
        logger.error(f"Error in manual search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Include API router
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        log_level="info"
    )