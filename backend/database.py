from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import os
from typing import List, Optional
import logging
from datetime import datetime

from models import User, Keyword, StoredListing, KeywordHit, Notification

logger = logging.getLogger(__name__)


class DatabaseManager:
    """MongoDB database manager"""
    
    def __init__(self):
        self.client: AsyncIOMotorClient = None
        self.db: AsyncIOMotorDatabase = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize database connection"""
        if self._initialized:
            return
        
        mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
        db_name = os.environ.get('DB_NAME', 'auction_bot_database')
        
        self.client = AsyncIOMotorClient(mongo_url)
        self.db = self.client[db_name]
        
        # Create indexes
        await self._create_indexes()
        self._initialized = True
        logger.info(f"Database initialized: {db_name}")
    
    async def _create_indexes(self):
        """Create necessary database indexes"""
        try:
            # Users collection indexes
            await self.db.users.create_index("telegram_id", unique=True)
            
            # Keywords collection indexes
            await self.db.keywords.create_index("user_id")
            await self.db.keywords.create_index([("user_id", 1), ("keyword", 1)], unique=True)
            
            # Listings collection indexes
            await self.db.listings.create_index([("platform", 1), ("platform_id", 1)], unique=True)
            await self.db.listings.create_index("first_seen_ts")
            
            # Keyword hits indexes
            await self.db.keyword_hits.create_index("keyword_id")
            await self.db.keyword_hits.create_index("user_id")
            await self.db.keyword_hits.create_index("seen_ts")
            
            # Notifications indexes
            await self.db.notifications.create_index("user_id")
            await self.db.notifications.create_index("sent_at")
            
            logger.info("Database indexes created successfully")
            
        except Exception as e:
            logger.error(f"Error creating indexes: {e}")
    
    # User operations
    async def create_user(self, user: User) -> User:
        """Create a new user"""
        user_dict = user.dict()
        await self.db.users.insert_one(user_dict)
        return user
    
    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Get user by telegram ID"""
        user_doc = await self.db.users.find_one({"telegram_id": telegram_id})
        if user_doc:
            return User(**user_doc)
        return None
    
    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID"""
        user_doc = await self.db.users.find_one({"id": user_id})
        if user_doc:
            return User(**user_doc)
        return None
    
    # Keyword operations
    async def create_keyword(self, keyword: Keyword) -> Keyword:
        """Create a new keyword"""
        keyword_dict = keyword.dict()
        await self.db.keywords.insert_one(keyword_dict)
        return keyword
    
    async def get_keyword_by_id(self, keyword_id: str) -> Optional[Keyword]:
        """Get keyword by ID"""
        keyword_doc = await self.db.keywords.find_one({"id": keyword_id})
        if keyword_doc:
            return Keyword(**keyword_doc)
        return None
    
    async def get_user_keywords(self, user_id: str, active_only: bool = False) -> List[Keyword]:
        """Get all keywords for a user"""
        query = {"user_id": user_id}
        if active_only:
            query["is_active"] = True
        
        keywords_cursor = self.db.keywords.find(query).sort("created_at", -1)
        keywords = await keywords_cursor.to_list(length=None)
        return [Keyword(**keyword) for keyword in keywords]
    
    async def get_user_keyword_by_text(self, user_id: str, keyword_text: str) -> Optional[Keyword]:
        """Get specific keyword by user and text"""
        keyword_doc = await self.db.keywords.find_one({
            "user_id": user_id,
            "keyword": keyword_text
        })
        if keyword_doc:
            return Keyword(**keyword_doc)
        return None
    
    async def update_keyword(self, keyword_id: str, update_data: dict) -> bool:
        """Update keyword"""
        update_data["updated_at"] = datetime.utcnow()
        result = await self.db.keywords.update_one(
            {"id": keyword_id},
            {"$set": update_data}
        )
        return result.modified_count > 0
    
    async def delete_keyword(self, keyword_id: str) -> bool:
        """Delete keyword"""
        result = await self.db.keywords.delete_one({"id": keyword_id})
        return result.deleted_count > 0
    
    async def get_all_active_keywords(self) -> List[Keyword]:
        """Get all active keywords across all users"""
        keywords_cursor = self.db.keywords.find({
            "is_active": True,
            "is_muted": False
        })
        keywords = await keywords_cursor.to_list(length=None)
        return [Keyword(**keyword) for keyword in keywords]
    
    # Listing operations
    async def create_or_update_listing(self, listing: StoredListing) -> StoredListing:
        """Create or update a listing (upsert by platform + platform_id)"""
        listing_dict = listing.dict()
        
        # Check if listing exists
        existing = await self.db.listings.find_one({
            "platform": listing.platform,
            "platform_id": listing.platform_id
        })
        
        if existing:
            # Update last_seen_ts
            await self.db.listings.update_one(
                {"platform": listing.platform, "platform_id": listing.platform_id},
                {"$set": {"last_seen_ts": listing.last_seen_ts}}
            )
            return StoredListing(**existing)
        else:
            # Insert new listing
            await self.db.listings.insert_one(listing_dict)
            return listing
    
    async def get_listing_by_platform_id(self, platform: str, platform_id: str) -> Optional[StoredListing]:
        """Get listing by platform and platform_id"""
        listing_doc = await self.db.listings.find_one({
            "platform": platform,
            "platform_id": platform_id
        })
        if listing_doc:
            return StoredListing(**listing_doc)
        return None
    
    # Keyword hit operations
    async def create_keyword_hit(self, hit: KeywordHit) -> KeywordHit:
        """Create a new keyword hit"""
        hit_dict = hit.dict()
        await self.db.keyword_hits.insert_one(hit_dict)
        return hit
    
    async def get_keyword_hit_count(self, keyword_id: str) -> int:
        """Get total hit count for keyword"""
        count = await self.db.keyword_hits.count_documents({"keyword_id": keyword_id})
        return count
    
    async def keyword_hit_exists(self, keyword_id: str, listing_id: str) -> bool:
        """Check if keyword hit already exists"""
        hit = await self.db.keyword_hits.find_one({
            "keyword_id": keyword_id,
            "listing_id": listing_id
        })
        return hit is not None
    
    # Notification operations
    async def create_notification(self, notification: Notification) -> Notification:
        """Create a new notification record"""
        notification_dict = notification.dict()
        await self.db.notifications.insert_one(notification_dict)
        return notification
    
    async def close(self):
        """Close database connection"""
        if self.client:
            self.client.close()


# Global database manager instance
db_manager = DatabaseManager()