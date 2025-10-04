from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
import uuid


@dataclass
class Listing:
    """Single listing from a provider search"""
    platform: str            # "militaria321.com"
    platform_id: str         # canonical ID from the platform
    title: str
    url: str
    price_value: Optional[float] = None
    price_currency: Optional[str] = None  # "EUR", "USD", etc.
    image_url: Optional[str] = None
    location: Optional[str] = None
    condition: Optional[str] = None
    seller_name: Optional[str] = None
    # Platform posting timestamp (parsed from detail page); stored in UTC
    posted_ts: Optional[datetime] = None
    # When auction/listing ends (if applicable)
    end_ts: Optional[datetime] = None


@dataclass
class SearchResult:
    """Result from a provider search operation"""
    items: List[Listing]
    total_count: Optional[int] = None  # total available results, if known
    has_more: bool = False
    pages_scanned: Optional[int] = None


# MongoDB Pydantic Models
class User(BaseModel):
    """User collection"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    telegram_id: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Keyword(BaseModel):
    """Keywords collection for user subscriptions"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    original_keyword: str  # Original keyword as entered by user
    normalized_keyword: str  # Unicode NFKC normalized, case-insensitive
    since_ts: datetime  # When subscription was created (UTC)
    seen_listing_keys: List[str] = Field(default_factory=list)  # ["platform:platform_id"]
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class StoredListing(BaseModel):
    """Listings collection"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    platform: str
    platform_id: str
    title: str
    url: str
    price_value: Optional[float] = None
    price_currency: Optional[str] = None
    image_url: Optional[str] = None
    location: Optional[str] = None
    condition: Optional[str] = None
    seller_name: Optional[str] = None
    first_seen_ts: datetime = Field(default_factory=datetime.utcnow)
    last_seen_ts: datetime = Field(default_factory=datetime.utcnow)
    posted_ts: Optional[datetime] = None  # Platform posting timestamp (UTC)
    end_ts: Optional[datetime] = None     # Listing end timestamp (UTC)


class Notification(BaseModel):
    """Notifications collection with idempotency"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    keyword_id: str
    listing_key: str  # "platform:platform_id" for uniqueness
    sent_at: datetime = Field(default_factory=datetime.utcnow)
