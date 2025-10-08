import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "article_hunter_bot"))

from models import Listing, Keyword  # noqa: E402
from services.search_service import SearchService  # noqa: E402


class DummyDB:
    def __init__(self):
        self.db = None


@pytest.fixture()
def search_service():
    return SearchService(DummyDB())


def make_keyword(since_ts: datetime) -> Keyword:
    return Keyword(
        user_id="u1",
        original_keyword="dolch",
        normalized_keyword="dolch",
        since_ts=since_ts,
        seen_listing_keys=[],
        platforms=["militaria321.com", "egun.de"],
    )


def make_listing(posted_ts: datetime | None) -> Listing:
    return Listing(
        platform="egun.de",
        platform_id="123",
        title="Dolch",
        url="https://www.egun.de/market/item.php?id=123",
        posted_ts=posted_ts,
    )


def test_is_new_listing_with_recent_posted_ts(search_service):
    now = datetime.now(timezone.utc)
    keyword = make_keyword(now - timedelta(hours=1))
    listing = make_listing(now - timedelta(minutes=10))
    assert search_service._is_new_listing(listing, keyword)


def test_is_new_listing_absorbs_old_posted_ts(search_service):
    now = datetime.now(timezone.utc)
    keyword = make_keyword(now - timedelta(hours=1))
    listing = make_listing(now - timedelta(hours=2))
    assert not search_service._is_new_listing(listing, keyword)


def test_is_new_listing_grace_window_allows_without_posted_ts(search_service):
    now = datetime.now(timezone.utc)
    keyword = make_keyword(now - timedelta(minutes=30))
    listing = make_listing(None)
    assert search_service._is_new_listing(listing, keyword)


def test_is_new_listing_grace_window_expires(search_service):
    now = datetime.now(timezone.utc)
    keyword = make_keyword(now - timedelta(hours=2))
    listing = make_listing(None)
    assert not search_service._is_new_listing(listing, keyword)
