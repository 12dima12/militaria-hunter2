from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Protocol
from models import Listing, SearchResult


class Provider(Protocol):
    """Provider interface for auction platforms"""
    name: str  # e.g., "militaria321.com"

    def search(self, keyword: str, since_ts: Optional[datetime] = None, sample_mode: bool = False) -> SearchResult:
        """Return search results.
        
        Args:
            keyword: Search term
            since_ts: Only return items newer than this timestamp (None for all)
            sample_mode: If True, fetch broader set for first-run sample display
        
        Returns:
            SearchResult with items, total_count, and has_more flag
        """
        ...

    def build_query(self, keyword: str) -> str:
        """Optional: platform-specific query building"""
        return keyword


class BaseProvider(ABC):
    """Base provider implementation"""
    
    def __init__(self, name: str):
        self.name = name
    
    @abstractmethod
    async def search(self, keyword: str, since_ts: Optional[datetime] = None, sample_mode: bool = False) -> SearchResult:
        """Search for listings matching the keyword"""
        pass
    
    def build_query(self, keyword: str) -> str:
        """Default query builder - can be overridden"""
        return keyword.strip().lower()