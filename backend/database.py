from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import os
from typing import List, Optional, Tuple
import logging
from datetime import datetime
import re

from models import User, Keyword, StoredListing, KeywordHit, Notification, DeleteAttemptLog
from utils.listing_key import extract_platform_id

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
        
        # Create indexes and verify
        await self._create_indexes()
        # Run one-time migration(s) that must precede scheduler/bot
        await self._migrate_notifications_listing_key()
        
        self._initialized = True
        logger.info(f"Database initialized: {db_name}")
    
    async def _create_indexes(self):
        """Create necessary database indexes"""
        try:
            # Users collection indexes
            await self.db.users.create_index("telegram_id", unique=True)
            
            # Keywords collection indexes
            await self.db.keywords.create_index("user_id")
            await self.db.keywords.create_index([("user_id", 1), ("normalized_keyword", 1)], unique=True)
            
            # Listings collection indexes
            await self.db.listings.create_index([("platform", 1), ("platform_id", 1)], unique=True)
            await self.db.listings.create_index("first_seen_ts")
            
            # Keyword hits indexes
            await self.db.keyword_hits.create_index("keyword_id")
            await self.db.keyword_hits.create_index("user_id")
            await self.db.keyword_hits.create_index("seen_ts")
            
            # Notifications indexes (with idempotency guard)
            await self.db.notifications.create_index("user_id")
            await self.db.notifications.create_index("sent_at")
            
            # Drop legacy unique indexes if present
            for idx_name in ["user_keyword_key_unique", "idempotency_guard", "user_keyword_listing_unique"]:
                try:
                    await self.db.notifications.drop_index(idx_name)
                except Exception:
                    pass
            
            # Create new partial unique index to exclude null/missing listing_key
            await self.db.notifications.create_index(
                [("user_id", 1), ("keyword_id", 1), ("listing_key", 1)],
                unique=True,
                name="user_keyword_listing_unique",
                partialFilterExpression={"listing_key": {"$type": "string"}}
            )
            
            logger.info("Database indexes created successfully")
            
            # Verify the unique index exists
            try:
                idxes = await self.db.notifications.index_information()
                verified = any(
                    name == "user_keyword_listing_unique" and info.get("unique")
                    for name, info in idxes.items()
                )
                logger.info(f"notifications_unique_index_verified={bool(verified)}")
            except Exception as ve:
                logger.warning(f"Could not verify notifications unique index: {ve}")
            
        except Exception as e:
            logger.error(f"Error creating indexes: {e}")
    
    async def _migrate_notifications_listing_key(self) -> None:
        """
        One-time migration: backfill notifications.listing_key where null/missing, then ensure unique index.
        Steps:
        - Find notifications with missing/null/empty listing_key
        - For each, fetch corresponding listing and derive canonical platform_id from URL (preferred) or use stored platform_id
        - Set listing_key = f"{platform}:{platform_id}"; if cannot derive, delete the notification (archive/delete policy)
        - Finally, (re)create the partial unique index
        """
        try:
            # Pause-like state: since called before scheduler/bot start, no concurrent writes
            cursor = self.db.notifications.find({
                "$or": [
                    {"listing_key": {"$exists": False}},
                    {"listing_key": None},
                    {"listing_key": ""}
                ]
            })
            total_scanned = 0
            backfilled = 0
            archived = 0
            async for notif in cursor:
                total_scanned += 1
                notif_id = notif.get("_id")
                listing_id = notif.get("listing_id")
                if not listing_id:
                    # No link to listing, cannot recover
                    await self.db.notifications.delete_one({"_id": notif_id})
                    archived += 1
                    logger.warning(f"Migration: deleted orphan notification {_id} (no listing_id)")
                    continue
                listing = await self.db.listings.find_one({"id": listing_id})
                if not listing:
                    # Orphaned notification
                    await self.db.notifications.delete_one({"_id": notif_id})
                    archived += 1
                    logger.warning(f"Migration: deleted orphan notification {notif_id} (listing not found)")
                    continue
                platform = listing.get("platform")
                url = listing.get("url")
                platform_id = ""
                try:
                    platform_id = extract_platform_id(platform, url)
                except Exception:
                    platform_id = ""
                if not platform_id:
                    platform_id = listing.get("platform_id") or ""
                if not platform or not platform_id:
                    # Still cannot construct stable key; delete notification
                    await self.db.notifications.delete_one({"_id": notif_id})
                    archived += 1
                    logger.warning(f"Migration: deleted notification {notif_id} (cannot derive listing_key)")
                    continue
                listing_key = f"{platform}:{platform_id}"
                await self.db.notifications.update_one({"_id": notif_id}, {"$set": {"listing_key": listing_key}})
                backfilled += 1
            # Rebuild the partial unique index again to ensure cleanliness
            for idx_name in ["user_keyword_key_unique", "idempotency_guard", "user_keyword_listing_unique"]:
                try:
                    await self.db.notifications.drop_index(idx_name)
                except Exception:
                    pass
            await self.db.notifications.create_index(
                [("user_id", 1), ("keyword_id", 1), ("listing_key", 1)],
                unique=True,
                name="user_keyword_listing_unique",
                partialFilterExpression={"listing_key": {"$type": "string"}}
            )
            remaining_null = await self.db.notifications.count_documents({
                "$or": [
                    {"listing_key": {"$exists": False}},
                    {"listing_key": None},
                    {"listing_key": ""}
                ]
            })
            logger.info({
                "event": "migration_report",
                "collection": "notifications",
                "total_scanned": total_scanned,
                "backfilled": backfilled,
                "archived": archived,
                "remaining_null": remaining_null,
            })
        except Exception as e:
            logger.error(f"Migration error (notifications listing_key): {e}")
    
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
        
        # Migrate keywords to add missing fields
        migrated_keywords = []
        for keyword_doc in keywords:
            needs_update = False
            update_fields = {}
            
            # Add normalized_keyword if missing
            if "normalized_keyword" not in keyword_doc or keyword_doc["normalized_keyword"] is None:
                normalized = keyword_doc["keyword"].strip().casefold()
                update_fields["normalized_keyword"] = normalized
                keyword_doc["normalized_keyword"] = normalized
                needs_update = True
            
            # Add since_ts if missing (use created_at or current time)
            if "since_ts" not in keyword_doc:
                since_ts = keyword_doc.get("created_at", datetime.utcnow())
                update_fields["since_ts"] = since_ts
                keyword_doc["since_ts"] = since_ts
                needs_update = True
            
            # Add seen_listing_keys if missing
            if "seen_listing_keys" not in keyword_doc:
                update_fields["seen_listing_keys"] = []
                keyword_doc["seen_listing_keys"] = []
                needs_update = True
            
            # Apply updates if needed
            if needs_update:
                await self.db.keywords.update_one(
                    {"_id": keyword_doc["_id"]},
                    {"$set": update_fields}
                )
            
            migrated_keywords.append(Keyword(**keyword_doc))
        
        return migrated_keywords
    
    async def get_user_keyword_by_normalized(self, user_id: str, normalized_keyword: str) -> Optional[Keyword]:
        """Get specific keyword by user and normalized text"""
        keyword_doc = await self.db.keywords.find_one({
            "user_id": user_id,
            "normalized_keyword": normalized_keyword
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
                {"$set": {"last_seen_ts": listing.last_seen_ts, "posted_ts": listing.posted_ts, "end_ts": listing.end_ts}}
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
    
    async def log_delete_attempt(self, log: DeleteAttemptLog) -> DeleteAttemptLog:
        """Log a delete attempt for telemetry"""
        log_dict = log.dict()
        await self.db.delete_attempt_logs.insert_one(log_dict)
        return log
    
    async def close(self):
        """Close database connection"""
        if self.client:
            self.client.close()
    
    async def add_to_seen_set_batch(self, keyword_id: str, listing_keys: List[str]) -> bool:
        """
        Add multiple listing keys to a keyword's seen_listing_keys set in batch.
        Uses $addToSet to ensure no duplicates.
        
        Args:
            keyword_id: The keyword ID
            listing_keys: List of "platform:platform_id" strings
            
        Returns:
            True if update succeeded
        """
        if not listing_keys:
            return True

    async def create_notification_idempotent(self, user_id: str, keyword_id: str, listing_key: str, notification_data: dict) -> bool:
        """
        Create a notification with idempotency guard.
        
        Returns:
            True if notification was created (first time)
            False if notification already exists (duplicate, skip sending)
        """
        try:
            # Guard: listing_key must be a non-empty string
            if not isinstance(listing_key, str) or not listing_key.strip():
                logger.warning("Refusing to insert notification without valid listing_key")
                return False
            # Add idempotency fields
            notification_data.update({
                "user_id": user_id,
                "keyword_id": keyword_id,
                "listing_key": listing_key,
                "sent_at": datetime.utcnow()
            })
            
            # Try to insert - will fail if duplicate key exists
            await self.db.notifications.insert_one(notification_data)
            return True
            
        except Exception as e:
            # Check if it's a duplicate key error
            if "duplicate key error" in str(e).lower() or "E11000" in str(e):
                logger.debug(f"Duplicate notification prevented: {listing_key}")
                return False
            else:
                # Other error - re-raise
                logger.error(f"Error creating notification: {e}")
                raise

        
        try:
            result = await self.db.keywords.update_one(
                {"id": keyword_id},
                {"$addToSet": {"seen_listing_keys": {"$each": listing_keys}}}
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            logger.error(f"Error adding to seen set for keyword {keyword_id}: {e}")
            return False

        """Close database connection"""
        if self.client:
            self.client.close()


# Global database manager instance
db_manager = DatabaseManager()