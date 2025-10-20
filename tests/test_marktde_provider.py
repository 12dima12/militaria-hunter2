import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

# Ensure provider package is importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "article_hunter_bot"))

from providers.marktde import MarktDeProvider  # noqa: E402


def _make_provider() -> MarktDeProvider:
    provider = MarktDeProvider()
    # Speed up unit tests
    provider.delay = 0
    provider.max_retries = 1
    return provider


def test_build_search_url_encodes_keyword():
    provider = _make_provider()
    url_page1 = provider.build_search_url("Küchen Messer")
    url_page2 = provider.build_search_url("Küchen Messer", page=2)

    assert url_page1 == "https://www.markt.de/suche/K%C3%BCchen%20Messer/"
    assert url_page2 == "https://www.markt.de/suche/K%C3%BCchen%20Messer/?page=2"


@pytest.mark.parametrize(
    "label, expected",
    [
        ("Heute, 10:30 Uhr", datetime(2025, 5, 1, 8, 30, tzinfo=timezone.utc)),
        ("Gestern, 22:10", datetime(2025, 4, 30, 20, 10, tzinfo=timezone.utc)),
        ("vor 2 Stunden", datetime(2025, 5, 1, 10, 0, tzinfo=timezone.utc)),
        ("12.04.2024 14:45", datetime(2024, 4, 12, 12, 45, tzinfo=timezone.utc)),
    ],
)
def test_parse_posted_ts_variants(label: str, expected: datetime):
    provider = _make_provider()
    fetched_at = datetime(2025, 5, 1, 12, 0, tzinfo=timezone.utc)
    result = provider._parse_posted_ts(label, fetched_at)
    assert result == expected


def test_parse_result_page_skips_partner_and_extracts_listing():
    provider = _make_provider()
    fetched_at = datetime(2025, 5, 1, 8, 0, tzinfo=timezone.utc)
    html = """
    <html>
      <body>
        <div class="clsy-c-result-list-item">
          <div class="clsy-c-result-list-item__partner">Partner-Anzeige</div>
          <a href="/promo/a/ffffffff/">Partner Anzeige</a>
        </div>
        <div class="clsy-c-result-list-item">
          <a class="clsy-c-result-list-item__title" href="/kuechenmesser-kuechenbeil-metgerbeil-hackbeil-fleischmesser-knochenmesser-carbon-edelstahl/a/50afe4e7/">
            Küchenmesser Küchenbeil
          </a>
          <div class="clsy-c-result-list-item__price">35 € VB</div>
          <div class="clsy-c-result-list-item__location">Berlin</div>
          <div class="clsy-c-result-list-item__time">Heute, 10:30 Uhr</div>
          <img src="/images/thumb.jpg" />
          <div class="clsy-c-result-list-item__seller">Privat</div>
        </div>
        <div class="clsy-c-result-list-item">
          <a href="/andere-waffe/a/a1b2c3d4/">Anderer Artikel</a>
          <div data-testid="result-item-price">1.250 €</div>
          <div data-testid="result-item-location">München</div>
          <div data-testid="result-item-published">vor 90 Minuten</div>
        </div>
        <nav>
          <a rel="next" href="/suche/messer/?page=2">Weiter</a>
        </nav>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")

    parsed = provider._parse_result_page(soup, fetched_at)

    assert parsed.organic_total == 2
    assert parsed.skipped_partner == 1
    assert parsed.has_more is True
    assert parsed.ids_on_page == ["50afe4e7", "a1b2c3d4"]

    listings = parsed.listings
    assert len(listings) == 2

    first = listings[0]
    assert first.title.strip() == "Küchenmesser Küchenbeil"
    assert first.platform_id == "50afe4e7"
    assert first.url.endswith("/a/50afe4e7/")
    assert first.price_value == pytest.approx(35.0)
    assert first.price_currency == "EUR"
    assert first.location == "Berlin"
    assert first.seller_name == "Privat"
    assert first.image_url.endswith("thumb.jpg")
    assert first.posted_ts == datetime(2025, 5, 1, 8, 30, tzinfo=timezone.utc)

    second = listings[1]
    assert second.platform_id == "a1b2c3d4"
    assert second.price_value == pytest.approx(1250.0)
    assert second.location == "München"
    assert second.posted_ts == datetime(2025, 5, 1, 6, 30, tzinfo=timezone.utc)
