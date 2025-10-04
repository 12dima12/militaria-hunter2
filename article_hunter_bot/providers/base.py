from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime
from models import SearchResult


class BaseProvider(ABC):
    """Base provider interface for extensible platform support"""
    
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return platform name (e.g. 'militaria321.com')"""
        pass
    
    @abstractmethod
    async def search(
        self, 
        keyword: str, 
        since_ts: Optional[datetime] = None,
        sample_mode: bool = False,
        crawl_all: bool = False
    ) -> SearchResult:
        """Search for listings matching keyword
        
        Args:
            keyword: Search term
            since_ts: Only return items newer than this timestamp (for polling)
            sample_mode: Return sample results for display (not used in requirements)
            crawl_all: Crawl all pages (baseline mode) vs first page only
            
        Returns:
            SearchResult with items, total_count, has_more, pages_scanned
        """
        pass
