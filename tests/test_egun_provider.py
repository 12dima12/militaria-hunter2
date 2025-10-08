import sys
from pathlib import Path
from datetime import datetime, timedelta

import pytest
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

# Ensure provider package is importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "article_hunter_bot"))

from providers.egun import EgunProvider  # noqa: E402


def test_parse_search_page_skips_sponsored_block():
    provider = EgunProvider()
    html = """
    <html><body>
        <table>
            <tr><td><a href="item.php?id=111">Anzeige 1</a></td></tr>
        </table>
        <b>Alle Artikel, die &quot;Dolch&quot; in Titel oder Beschreibung enthalten</b>
        <table>
            <tr>
                <td><img src="images/pic1.jpg" /></td>
                <td><a href="item.php?id=222">Dolch A</a></td>
                <td>1.234,00 €</td>
            </tr>
            <tr>
                <td><a href="item.php?id=333">Dolch B</a></td>
                <td>345,67 EUR</td>
            </tr>
        </table>
    </body></html>
    """

    soup = BeautifulSoup(html, "html.parser")
    items, total_count, has_more = provider._parse_search_page(soup, "Dolch", 1)

    assert not has_more
    assert total_count is None
    assert [item.platform_id for item in items] == ["222", "333"]
    assert pytest.approx(items[0].price_value, rel=1e-3) == 1234.0
    assert items[0].image_url.endswith("images/pic1.jpg")


def test_extract_posted_ts_direct_label():
    provider = EgunProvider()
    html = """
    <html><body>
        <table>
            <tr><th>Auktionsbeginn</th><td>07.10.2025 16:13:19 Uhr</td></tr>
        </table>
    </body></html>
    """

    soup = BeautifulSoup(html, "html.parser")
    posted, mode = provider._extract_posted_ts(soup)

    expected = datetime(2025, 10, 7, 16, 13, 19, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(ZoneInfo("UTC"))
    assert posted == expected
    assert mode == "direct"


def test_compute_posted_ts_from_laufzeit():
    provider = EgunProvider()
    html = """
    <html><body>
        <table>
            <tr><th>Laufzeit</th><td>30 Tage</td></tr>
            <tr><th>vorauss. Ende</th><td>Di, 07.10.2025 16:13:19</td></tr>
        </table>
    </body></html>
    """

    soup = BeautifulSoup(html, "html.parser")
    posted, mode = provider._extract_posted_ts(soup)

    expected_end = datetime(2025, 10, 7, 16, 13, 19, tzinfo=ZoneInfo("Europe/Berlin")).astimezone(ZoneInfo("UTC"))
    expected_start = (expected_end - timedelta(days=30))

    assert posted is not None
    assert mode == "computed"
    assert int(posted.timestamp()) == int(expected_start.timestamp())


def test_matches_keyword_whole_word():
    provider = EgunProvider()
    assert provider.matches_keyword("Historischer Dolch", "dolch")
    assert not provider.matches_keyword("Dolche", "dolch")
    assert not provider.matches_keyword("12:00 Uhr Start", "uhr")
