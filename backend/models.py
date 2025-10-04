from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
import uuid


@dataclass
class Listing:
    """Normalized listing schema for militaria321.com"""
    platform: str            # "militaria321.com"
    platform_id: str         # canonical ID from the platform
    title: str
    url: str
    price_value: Optional[float] = None
    price_currency: Optional[str] = None  # "EUR", "USD", etc.
    location: Optional[str] = None
    condition: Optional[str] = None
    seller_name: Optional[str] = None
    seller_rating: Optional[float] = None
    listing_type: Optional[str] = None    # "auction", "buy_now", etc.
    image_url: Optional[str] = None
    first_seen_ts: datetime = Field(default_factory=lambda: datetime.utcnow())
    last_seen_ts: datetime = Field(default_factory=lambda: datetime.utcnow())


@dataclass
class SearchResult:
    """Result from a search operation"""
    items: List[Listing]
    total_count: Optional[int] = None  # total available results, if known
    has_more: bool = False


# MongoDB Pydantic Models
class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Keyword(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    keyword: str  # Original keyword as entered by user
    normalized_keyword: Optional[str] = None  # Case-insensitive normalized version using casefold()
    is_active: bool = True
    is_muted: bool = False
    muted_until: Optional[datetime] = None
    frequency_seconds: int = 60  # Default 60 seconds
    platforms: List[str] = Field(default_factory=lambda: ["militaria321.com"])
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_checked: Optional[datetime] = None
    first_run_completed: bool = False  # Track if first-run sample was shown
    since_ts: datetime = Field(default_factory=datetime.utcnow)  # When subscription was created/updated
    seen_listing_keys: List[str] = Field(default_factory=list)  # Track seen (platform, platform_id) as strings


class StoredListing(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    platform: str
    platform_id: str
    title: str
    url: str
    price_value: Optional[float] = None
    price_currency: Optional[str] = None
    location: Optional[str] = None
    condition: Optional[str] = None
    seller_name: Optional[str] = None
    seller_rating: Optional[float] = None
    listing_type: Optional[str] = None
    image_url: Optional[str] = None
    first_seen_ts: datetime = Field(default_factory=datetime.utcnow)
    last_seen_ts: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        # Ensure (platform, platform_id) uniqueness in MongoDB
        pass


class KeywordHit(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    keyword_id: str
    listing_id: str
    user_id: str
    seen_ts: datetime = Field(default_factory=datetime.utcnow)
    notified: bool = False
    is_sample: bool = False  # Mark if this was from first-run sample


class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    keyword_id: str
    listing_id: str
    telegram_message_id: Optional[int] = None
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "sent"  # sent, failed, cancelled
    notification_type: str = "new_item"  # new_item, first_run_sample


class DeleteAttemptLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    normalized_keyword: str
    original_keyword: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    telegram_message_id: Optional[int] = None