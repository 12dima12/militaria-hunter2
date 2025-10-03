from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Protocol
from models import Listing


class Provider(Protocol):
    """Provider interface for auction platforms"""
    name: str  # e.g., "militaria321.com"

    def search(self, keyword: str, since_ts: Optional[datetime] = None) -> List[Listing]:
        """Return new/updated listings since since_ts (or all if None)."""
        ...

    def build_query(self, keyword: str) -> str:
        """Optional: platform-specific query building"""
        return keyword


class BaseProvider(ABC):
    """Base provider implementation"""
    
    def __init__(self, name: str):
        self.name = name
    
    @abstractmethod
    async def search(self, keyword: str, since_ts: Optional[datetime] = None) -> List[Listing]:
        """Search for listings matching the keyword"""
        pass
    
    def build_query(self, keyword: str) -> str:
        """Default query builder - can be overridden"""
        return keyword.strip().lower()