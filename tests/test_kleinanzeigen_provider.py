from datetime import datetime, timezone

import pytest

from article_hunter_bot.providers.kleinanzeigen import KleinanzeigenProvider


def test_build_search_url_encoding():
    provider = KleinanzeigenProvider()

    url = provider.build_search_url("Wehrmacht Helm", page=1)
    assert url == "https://www.kleinanzeigen.de/s-wehrmacht-helm/"

    url2 = provider.build_search_url("Ä Ö ß", page=3)
    assert url2 == "https://www.kleinanzeigen.de/s-seite:3/%C3%A4-%C3%B6-%C3%9F/"


def test_build_search_url_clamps_to_max_page():
    provider = KleinanzeigenProvider()

    url = provider.build_search_url("dolch", page=99)
    assert url.endswith("/s-seite:50/dolch/")


def test_candidate_paths_clamp_to_hard_limit():
    provider = KleinanzeigenProvider()

    paths = provider._candidate_paths("test", 88)
    assert all("seite:50" in path for path in paths if "seite" in path)


def test_fresh_pages_after_baseline_clamped(monkeypatch):
    monkeypatch.setenv("KA_FRESH_PAGES_AFTER_BASELINE", "80")
    provider = KleinanzeigenProvider()

    assert provider.fresh_pages_after_baseline == provider.MAX_PAGE_HARD_LIMIT


def test_extract_platform_id_from_various_urls():
    provider = KleinanzeigenProvider()

    assert provider._extract_platform_id("https://www.kleinanzeigen.de/s-anzeige/foo/1234567890-0") == "1234567890"
    assert provider._extract_platform_id("https://www.kleinanzeigen.de/profi-anzeige-987654321") == "987654321"
    assert provider._extract_platform_id("https://www.kleinanzeigen.de/s-anzeige/test/12345") == "12345"


def test_parse_posted_ts_text_absolute():
    provider = KleinanzeigenProvider()

    parsed = provider._parse_posted_ts_text("Online seit 12.05.2024, 13:45")
    assert parsed == datetime(2024, 5, 12, 11, 45, tzinfo=timezone.utc)

    parsed_no_time = provider._parse_posted_ts_text("Online seit 12.05.2024")
    assert parsed_no_time == datetime(2024, 5, 11, 22, 0, tzinfo=timezone.utc)


def test_parse_search_page_filters_promoted():
    provider = KleinanzeigenProvider()

    sample_html = """
    <html>
      <body>
        <div>Sponsored header</div>
        <h2>Alle Artikel, die wir gefunden haben</h2>
        <section>
          <article class="aditem" data-adid="123456789">
            <a href="/s-anzeige/test-angebot/123456789-0">Test Angebot</a>
            <span class="price">120 € VB</span>
          </article>
          <article class="aditem topad" data-adid="999">
            <a href="/s-anzeige/top-angebot/999-0">Anzeige</a>
            <span class="price">50 €</span>
          </article>
        </section>
      </body>
    </html>
    """

    results = provider._parse_search_page(sample_html)
    assert len(results) == 1
    listing = results[0]
    assert listing.platform_id == "123456789"
    assert listing.title == "Test Angebot"
    assert listing.price_value == 120.0
    assert listing.price_currency == "EUR"
    assert listing.price_text == "120 € VB"
    assert listing.platform == "kleinanzeigen.de"


def test_detect_consent_banner_ids():
    html = """
    <html>
      <body>
        <div id="gdpr-banner-title">Willkommen bei Kleinanzeigen</div>
        <button id="gdpr-banner-accept">Alle akzeptieren</button>
      </body>
    </html>
    """

    assert KleinanzeigenProvider.detect_consent(html) is True


def test_detect_consent_without_cards():
    html = """
    <html>
      <body>
        <div id="gdpr-banner-cmp-button">Einstellungen</div>
      </body>
    </html>
    """

    assert KleinanzeigenProvider.detect_consent(html) is True


def test_detect_consent_false_when_results_present():
    html = """
    <html>
      <body>
        <article data-adid="123"></article>
        <div>Kein Banner</div>
      </body>
    </html>
    """

    assert KleinanzeigenProvider.detect_consent(html) is False

