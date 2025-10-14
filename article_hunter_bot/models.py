import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
import uuid

from utils.time_utils import now_utc


@dataclass
class Listing:
    """Single listing from a provider search"""
    platform: str            # "militaria321.com"
    platform_id: str         # canonical ID from the platform
    title: str
    url: str
    price_value: Optional[float] = None
    price_currency: Optional[str] = None  # "EUR", "USD", etc.
    price_text: Optional[str] = None      # Raw price text if parsing unreliable
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
    last_page_index: Optional[int] = None  # Last page index processed
    metadata: Optional[dict] = None


# MongoDB Pydantic Models
class User(BaseModel):
    """User collection"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    telegram_id: int
    created_at: datetime = Field(default_factory=now_utc)


def _default_platforms() -> List[str]:
    """Return default platform list while honoring Kleinanzeigen toggle"""
    default = ["militaria321.com", "egun.de"]
    enable_ka = os.environ.get("ENABLE_KLEINANZEIGEN", "true").strip().lower()
    if enable_ka not in {"0", "false", "off"}:
        default.append("kleinanzeigen.de")
    return default


class Keyword(BaseModel):
    """Keywords collection for user subscriptions"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    original_keyword: str  # Original keyword as entered by user
    normalized_keyword: str  # Unicode NFKC normalized, case-insensitive
    since_ts: datetime = Field(default_factory=now_utc)  # When subscription was created (UTC)
    seen_listing_keys: List[str] = Field(default_factory=list)  # ["platform:platform_id"]
    is_active: bool = True
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    
    # Telemetry fields for health monitoring
    baseline_status: str = "pending"  # {"pending","running","partial","error","complete"}
    baseline_started_ts: Optional[datetime] = None  # UTC
    baseline_completed_ts: Optional[datetime] = None  # UTC
    baseline_pages_scanned: dict = Field(default_factory=dict)  # platform → pages
    baseline_items_collected: dict = Field(default_factory=dict)  # platform → items
    baseline_errors: dict = Field(default_factory=dict)  # provider → message
    last_checked: Optional[datetime] = None  # UTC
    last_success_ts: Optional[datetime] = None  # UTC
    last_error_ts: Optional[datetime] = None  # UTC
    last_error_message: Optional[str] = None
    consecutive_errors: int = 0  # reset to 0 on success; +1 on failure
    platforms: List[str] = Field(default_factory=_default_platforms)
    
    # Poll-related fields for deep pagination
    poll_cursor_page: int = 1  # Current page position in rotating deep-scan
    total_pages_estimate: Optional[int] = None  # Estimated total pages for this keyword
    poll_mode: str = "full"  # "full" or "rotate" 
    poll_window: int = 12  # Number of pages in rotating window
    last_deep_scan_at: Optional[datetime] = None  # Last time full deep scan was done


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
    first_seen_ts: datetime = Field(default_factory=now_utc)
    last_seen_ts: datetime = Field(default_factory=now_utc)
    posted_ts: Optional[datetime] = None  # Platform posting timestamp (UTC)
    end_ts: Optional[datetime] = None     # Listing end timestamp (UTC)


class Notification(BaseModel):
    """Notifications collection with idempotency"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    keyword_id: str
    listing_key: str  # "platform:platform_id" for uniqueness
    sent_at: datetime = Field(default_factory=now_utc)
