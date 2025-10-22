import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

# Ensure provider package is importable when running via pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "article_hunter_bot"))

from providers.marktde import MarktDeProvider  # noqa: E402


def _make_provider() -> MarktDeProvider:
    provider = MarktDeProvider()
    provider.delay = 0  # Speed up tests
    provider.max_retries = 1
    return provider


def test_build_search_url_encodes_keyword() -> None:
    provider = _make_provider()
    url_page1 = provider.build_search_url("Küchen Messer")
    url_page2 = provider.build_search_url("Küchen Messer", page=2)

    assert url_page1 == "https://www.markt.de/suche/k%C3%BCchen+messer/"
    assert url_page2 == "https://www.markt.de/suche/k%C3%BCchen+messer/?page=2"


def test_extract_page_listings_skips_partner_and_duplicates() -> None:
    provider = _make_provider()
    html = """
    <html>
      <body>
        <div class="clsy-c-result-list-item">
          <div class="clsy-c-result-list-item__partner">Partner-Anzeige</div>
          <a href="/promo/a/ffffffff/">Partner Anzeige</a>
        </div>
        <div class="clsy-c-result-list-item">
          <a href="/kuechenmesser/a/50afe4e7/">Küchenmesser Küchenbeil</a>
          <div class="clsy-c-result-list-item__price">35 € VB</div>
          <div class="clsy-c-result-list-item__location">Berlin</div>
          <img src="/images/thumb.jpg" />
        </div>
        <div class="clsy-c-result-list-item">
          <a href="/kuechenmesser/a/50afe4e7/">Duplicate Link</a>
        </div>
        <div class="clsy-c-result-list-item">
          <a href="/andere-waffe/a/a1b2c3d4/">Anderer Artikel</a>
          <div data-testid="result-item-price">1.250 €</div>
          <div data-testid="result-item-location">München</div>
        </div>
        <div class="clsy-c-result-list-item">
          <a href="/katalog/a/bb11cc22/"></a>
        </div>
        <nav>
          <a rel="next" href="/suche/messer/?page=2">Weiter</a>
        </nav>
      </body>
    </html>
    """

    soup = BeautifulSoup(html, "html.parser")
    page_data = provider._extract_page_listings(soup)

    assert page_data["anchor_total"] >= 5
    assert page_data["anchor_with_id"] == 5
    assert page_data["partner_skipped"] == 1
    assert page_data["selector_miss"] == 1
    assert page_data["maybe_has_more"] is True

    listings = page_data["listings"]
    assert len(listings) == 2

    first = listings[0]
    assert first.platform_id == "50afe4e7"
    assert first.url.endswith("/a/50afe4e7/")
    assert first.price_value == 35.0
    assert first.price_currency == "EUR"
    assert first.location == "Berlin"
    assert first.posted_ts is None

    second = listings[1]
    assert second.platform_id == "a1b2c3d4"
    assert second.price_value == 1250.0
    assert second.location == "München"


def test_parse_timestamp_relative_and_absolute() -> None:
    provider = _make_provider()
    fixed_now = datetime(2024, 5, 20, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    provider._now_berlin = lambda: fixed_now  # type: ignore[assignment]

    absolute = provider._parse_timestamp_text("19.05.2024, 08:15 Uhr")
    assert absolute == datetime(2024, 5, 19, 6, 15, tzinfo=timezone.utc)

    heute = provider._parse_timestamp_text("Heute, 14:30 Uhr")
    assert heute == datetime(2024, 5, 20, 12, 30, tzinfo=timezone.utc)

    vor_std = provider._parse_timestamp_text("vor 3 Std.")
    assert vor_std == datetime(2024, 5, 20, 9, 0, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(timezone.utc)


def test_search_keeps_items_without_posted_ts(monkeypatch) -> None:
    provider = _make_provider()

    html = """
    <html>
      <body>
        <div class="clsy-c-result-list-item">
          <a href="/messer/a/50afe4e7/">Fund</a>
          <div class="clsy-c-result-list-item__price">10 €</div>
        </div>
      </body>
    </html>
    """

    async def fake_get(self, url, *args, **kwargs):  # type: ignore[override]
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=html, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get, raising=False)

    since_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = asyncio.run(
        provider.search("messer", since_ts=since_ts, crawl_all=False)
    )

    assert len(result.items) == 1
    first = result.items[0]
    assert first.platform_id == "50afe4e7"
    assert first.posted_ts is None
    assert result.metadata is not None
    assert result.metadata["drop_counts"]["dropped_no_ts"] == 0
