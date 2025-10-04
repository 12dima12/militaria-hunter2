from typing import List, Optional
import logging
from datetime import datetime

from models import Keyword
from database import DatabaseManager

logger = logging.getLogger(__name__)


class KeywordService:
    """Service for managing keywords"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    @staticmethod
    def normalize_keyword(keyword: str) -> str:
        """Normalize keyword using Unicode casefold for case-insensitive operations"""
        return keyword.strip().casefold()
    
    async def create_keyword(self, user_id: str, keyword_text: str, platforms: List[str] = None) -> Keyword:
        """Create a new keyword for user (case-insensitive)"""
        if platforms is None:
            platforms = ["militaria321.com"]
        
        normalized = self.normalize_keyword(keyword_text)
        
        keyword = Keyword(
            user_id=user_id,
            keyword=keyword_text,  # Store original as entered
            normalized_keyword=normalized,  # Store normalized for matching
            platforms=platforms,
            frequency_seconds=60  # Default 60 seconds
        )
        
        return await self.db.create_keyword(keyword)
    
    async def get_user_keywords(self, user_id: str) -> List[Keyword]:
        """Get all keywords for user"""
        return await self.db.get_user_keywords(user_id)
    
    async def get_user_keyword(self, user_id: str, keyword_text: str) -> Optional[Keyword]:
        """Get specific user keyword by text"""
        return await self.db.get_user_keyword_by_text(user_id, keyword_text)
    
    async def get_keyword_by_id(self, keyword_id: str) -> Optional[Keyword]:
        """Get keyword by ID"""
        return await self.db.get_keyword_by_id(keyword_id)
    
    async def update_keyword_status(self, keyword_id: str, is_active: bool = None, is_muted: bool = None, 
                                   muted_until: datetime = None) -> bool:
        """Update keyword status"""
        update_data = {}
        
        if is_active is not None:
            update_data["is_active"] = is_active
        
        if is_muted is not None:
            update_data["is_muted"] = is_muted
            if is_muted and muted_until:
                update_data["muted_until"] = muted_until
            elif not is_muted:
                update_data["muted_until"] = None
        
        if not update_data:
            return False
        
        return await self.db.update_keyword(keyword_id, update_data)
    
    async def update_keyword_frequency(self, keyword_id: str, frequency_seconds: int) -> bool:
        """Update keyword frequency"""
        return await self.db.update_keyword(keyword_id, {"frequency_seconds": frequency_seconds})
    
    async def update_last_checked(self, keyword_id: str, last_checked: datetime = None) -> bool:
        """Update last checked timestamp"""
        if last_checked is None:
            last_checked = datetime.utcnow()
        
        return await self.db.update_keyword(keyword_id, {"last_checked": last_checked})
    
    async def delete_keyword(self, keyword_id: str) -> bool:
        """Delete keyword"""
        return await self.db.delete_keyword(keyword_id)
    
    async def get_all_active_keywords(self) -> List[Keyword]:
        """Get all active keywords for monitoring"""
        return await self.db.get_all_active_keywords()
    
    async def get_keyword_hit_count(self, keyword_id: str) -> int:
        """Get total hit count for keyword"""
        return await self.db.get_keyword_hit_count(keyword_id)
    
    async def update_keyword_first_run(self, keyword_id: str, completed: bool = True) -> bool:
        """Mark keyword first run as completed"""
        return await self.db.update_keyword(keyword_id, {"first_run_completed": completed})