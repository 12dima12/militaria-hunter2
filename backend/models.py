from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
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
    # When we first saw the item during scraping (not the platform posting time)
    first_seen_ts: datetime = Field(default_factory=lambda: datetime.utcnow())
    last_seen_ts: datetime = Field(default_factory=lambda: datetime.utcnow())
    # Platform posting/auction start timestamp (parsed from detail page); stored in UTC
    posted_ts: Optional[datetime] = None


@dataclass
class SearchResult:
    """Result from a search operation"""
    items: List[Listing]
    total_count: Optional[int] = None  # total available results, if known
    has_more: bool = False
    pages_scanned: Optional[int] = None


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
    platforms: List[str] = Field(default_factory=lambda: ["egun.de", "militaria321.com"])  # Alphabetical order
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_checked: Optional[datetime] = None
    first_run_completed: bool = False  # Track if first-run sample was shown
    since_ts: datetime = Field(default_factory=datetime.utcnow)  # When subscription was created/updated
    seen_listing_keys: List[str] = Field(default_factory=list)  # Track seen (platform, platform_id) as strings
    provider_stats: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # Per-provider stats: {platform: {total_hits, last_poll_ts, last_match_ts, error_count}}
    baseline_status: str = "pending"  # Status: pending, partial, complete, error
    baseline_errors: Dict[str, str] = Field(default_factory=dict)  # Per-provider errors: {platform: error_message}


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
    posted_ts: Optional[datetime] = None  # Platform posting/auction start timestamp (UTC)
    end_ts: Optional[datetime] = None     # Auction/listing end timestamp (UTC)

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
    listing_key: str  # "platform:platform_id" for idempotency
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