import os
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, List
import logging
from datetime import datetime
from models import User, Keyword, StoredListing, Notification

logger = logging.getLogger(__name__)


class DatabaseManager:
    """MongoDB database manager"""
    
    def __init__(self):
        self.client = None
        self.db = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize database connection"""
        if self._initialized:
            return
        
        mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
        db_name = os.environ.get('DB_NAME', 'article_hunter')
        
        try:
            self.client = AsyncIOMotorClient(mongo_url)
            self.db = self.client[db_name]
            
            # Create indexes
            await self._create_indexes()
            
            # Test connection
            await self.client.admin.command('ping')
            logger.info(f"Connected to MongoDB: {db_name}")
            
            self._initialized = True
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
    
    async def _create_indexes(self):
        """Create necessary database indexes"""
        # Users: unique telegram_id
        await self.db.users.create_index("telegram_id", unique=True)
        
        # Keywords: user_id + normalized_keyword compound index
        await self.db.keywords.create_index([("user_id", 1), ("normalized_keyword", 1)])
        
        # Listings: unique platform + platform_id
        await self.db.listings.create_index([("platform", 1), ("platform_id", 1)], unique=True)
        
        # Notifications: unique idempotency index
        await self.db.notifications.create_index(
            [("user_id", 1), ("keyword_id", 1), ("listing_key", 1)],
            unique=True,
            partialFilterExpression={"listing_key": {"$type": "string"}}
        )
        
        logger.info("Database indexes created")
    
    # User operations
    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Get user by telegram ID"""
        doc = await self.db.users.find_one({"telegram_id": telegram_id})
        return User(**doc) if doc else None
    
    async def create_user(self, user: User) -> User:
        """Create new user"""
        doc = user.dict()
        await self.db.users.insert_one(doc)
        return user
    
    # Keyword operations
    async def get_user_keywords(self, user_id: str, active_only: bool = True) -> List[Keyword]:
        """Get user's keywords"""
        filter_dict = {"user_id": user_id}
        if active_only:
            filter_dict["is_active"] = True
        
        cursor = self.db.keywords.find(filter_dict)
        return [Keyword(**doc) async for doc in cursor]
    
    async def get_keyword_by_normalized(self, user_id: str, normalized_keyword: str) -> Optional[Keyword]:
        """Get keyword by normalized text"""
        doc = await self.db.keywords.find_one({
            "user_id": user_id,
            "normalized_keyword": normalized_keyword
        })
        return Keyword(**doc) if doc else None
    
    async def create_keyword(self, keyword: Keyword) -> Keyword:
        """Create new keyword"""
        doc = keyword.dict()
        await self.db.keywords.insert_one(doc)
        return keyword
    
    async def update_keyword_seen_keys(self, keyword_id: str, seen_keys: List[str]):
        """Update seen listing keys for keyword"""
        await self.db.keywords.update_one(
            {"id": keyword_id},
            {"$set": {"seen_listing_keys": seen_keys, "updated_at": datetime.utcnow()}}
        )
    
    async def delete_keyword(self, keyword_id: str):
        """Delete keyword"""
        await self.db.keywords.delete_one({"id": keyword_id})
    
    # Listing operations
    async def upsert_listing(self, listing: StoredListing) -> StoredListing:
        """Insert or update listing"""
        doc = listing.dict()
        
        await self.db.listings.update_one(
            {"platform": listing.platform, "platform_id": listing.platform_id},
            {"$set": doc},
            upsert=True
        )
        return listing
    
    # Notification operations
    async def create_notification(self, notification: Notification) -> bool:
        """Create notification if not duplicate (returns True if created)"""
        try:
            doc = notification.dict()
            await self.db.notifications.insert_one(doc)
            return True
        except Exception as e:
            # Duplicate key error (idempotency)
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                return False
            raise
    
    async def close(self):
        """Close database connection"""
        if self.client:
            self.client.close()
            logger.info("Database connection closed")
