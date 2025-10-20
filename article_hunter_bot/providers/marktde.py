"""Provider implementation for markt.de organic listings."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional, Tuple
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from models import Listing, SearchResult
from providers.base import BaseProvider
from utils.datetime_utils import BERLIN, now_utc, to_utc_aware

logger = logging.getLogger(__name__)

LISTING_ID_RE = re.compile(r"/a/([0-9a-fA-F]{8})/")
RELATIVE_TIME_RE = re.compile(r"vor\s+(?P<value>\d+)\s+(?P<unit>\w+)", re.IGNORECASE)
ABSOLUTE_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})[.](?P<month>\d{1,2})[.](?:(?P<year>\d{4})|)(?:\s|,)+(?P<hour>\d{1,2})[:](?P<minute>\d{2})",
    re.IGNORECASE,
)


@dataclass
class _PageParseResult:
    """Container summarising a parsed result page."""

    listings: list[Listing]
    organic_total: int
    skipped_partner: int
    ids_on_page: list[str]
    has_more: bool


class MarktDeProvider(BaseProvider):
    """Scraper for markt.de search listings with polite throttling."""

    BASE_URL = "https://www.markt.de"
    PLATFORM = "markt.de"

    def __init__(self) -> None:
        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        }
        self.timeout = float(os.environ.get("MARKTDE_TIMEOUT_SEC", "30"))
        self.delay = float(os.environ.get("MARKTDE_DELAY_SEC", "0.4"))
        self.max_retries = int(os.environ.get("MARKTDE_MAX_RETRIES", "3"))
        self.resolver = "httpx"

    @property
    def platform_name(self) -> str:
        return self.PLATFORM

    def build_search_url(self, keyword: str, page: int = 1) -> str:
        """Construct search URL respecting pagination conventions."""

        safe_keyword = quote(keyword.strip(), safe="")
        path = f"/suche/{safe_keyword}/"
        if page and page > 1:
            return urljoin(self.BASE_URL, f"{path}?page={page}")
        return urljoin(self.BASE_URL, path)

    async def search(
        self,
        keyword: str,
        since_ts: Optional[datetime] = None,
        sample_mode: bool = False,
        crawl_all: bool = False,
        max_pages_override: Optional[int] = None,
        mode: Optional[str] = None,
        poll_pages: Optional[int] = None,
        page_start: Optional[int] = None,
    ) -> SearchResult:
        """Search markt.de for the supplied keyword."""

        metadata = {
            "resolver": self.resolver,
            "consent_status": "not_detected",
            "mode": mode or ("baseline" if crawl_all else "poll"),
        }
        all_listings: list[Listing] = []
        seen_ids: set[str] = set()
        pages_scanned = 0
        has_more = False
        last_page_index = 0

        client_headers = dict(self.headers)
        timeout = httpx.Timeout(self.timeout)
        mode_label = metadata["mode"]
        max_pages = max_pages_override or (poll_pages if poll_pages else None)

        async with httpx.AsyncClient(
            headers=client_headers,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            page = page_start if page_start and page_start > 0 else 1
            previous_page_ids: Optional[list[str]] = None

            while True:
                if max_pages is not None and pages_scanned >= max_pages:
                    has_more = True
                    break

                url = self.build_search_url(keyword, page)
                fetched_at = now_utc()
                response: Optional[httpx.Response] = None
                error_message: Optional[str] = None

                for attempt in range(1, self.max_retries + 1):
                    try:
                        logger.info(
                            {
                                "event": "md_request",
                                "url": url,
                                "attempt": attempt,
                                "mode": mode_label,
                                "page": page,
                            }
                        )
                        response = await client.get(url)
                        break
                    except httpx.HTTPError as exc:  # pragma: no cover - network failure path
                        error_message = str(exc)
                        if attempt >= self.max_retries:
                            break
                        await asyncio.sleep(min(2.0, self.delay * (2 ** (attempt - 1))))

                if response is None:
                    metadata["error"] = error_message or "Unbekannter HTTP-Fehler"
                    metadata["status"] = "http_error"
                    break

                logger.info(
                    {
                        "event": "md_search",
                        "q": keyword,
                        "url": url,
                        "status": response.status_code,
                        "final_url": str(response.url),
                        "page": page,
                        "mode": mode_label,
                    }
                )

                if response.status_code >= 400:
                    metadata["error"] = f"HTTP {response.status_code}"
                    metadata["status"] = "http_error"
                    break

                soup = BeautifulSoup(response.text, "html.parser")

                if self._detect_consent_block(soup):
                    metadata["error"] = "Consent-Seite blockiert die Suche"
                    metadata["status"] = "consent_blocked"
                    metadata["consent_status"] = "blocked"
                    break

                parsed = self._parse_result_page(soup, fetched_at)

                logger.info(
                    {
                        "event": "md_page",
                        "q": keyword,
                        "page": page,
                        "items_total": parsed.organic_total,
                        "items_skipped_partner": parsed.skipped_partner,
                        "items_kept": len(parsed.listings),
                        "url": url,
                        "mode": mode_label,
                    }
                )

                if not parsed.listings:
                    has_more = False
                    break

                if previous_page_ids is not None and parsed.ids_on_page == previous_page_ids:
                    has_more = False
                    break

                for listing in parsed.listings:
                    if listing.platform_id in seen_ids:
                        continue
                    seen_ids.add(listing.platform_id)
                    all_listings.append(listing)

                pages_scanned += 1
                last_page_index = page
                has_more = parsed.has_more
                previous_page_ids = parsed.ids_on_page

                if not crawl_all and max_pages_override is None and not (poll_pages and poll_pages > 1):
                    break

                page += 1
                await asyncio.sleep(self.delay)

        logger.info(
            {
                "event": "md_search_summary",
                "q": keyword,
                "pages": pages_scanned,
                "items": len(all_listings),
                "has_more": has_more,
                "resolver": metadata.get("resolver"),
                "consent_status": metadata.get("consent_status"),
                "error": metadata.get("error"),
                "mode": mode_label,
            }
        )

        metadata["pages_scanned"] = pages_scanned
        metadata["last_page_index"] = last_page_index
        metadata["has_more"] = has_more

        return SearchResult(
            items=all_listings,
            has_more=has_more,
            pages_scanned=pages_scanned,
            last_page_index=last_page_index,
            metadata=metadata,
        )

    def _parse_result_page(self, soup: BeautifulSoup, fetched_at: datetime) -> _PageParseResult:
        """Parse organic listing cards from a search result page."""

        organic_cards: list[Tag] = []
        skipped_partner = 0

        for card in self._iter_cards(soup):
            if self._is_partner_card(card):
                skipped_partner += 1
                continue
            organic_cards.append(card)

        listings: list[Listing] = []
        ids_on_page: list[str] = []

        for card in organic_cards:
            listing = self._extract_listing(card, fetched_at)
            if listing is None:
                continue
            listings.append(listing)
            ids_on_page.append(listing.platform_id)

        has_more = self._detect_has_more(soup)
        return _PageParseResult(
            listings=listings,
            organic_total=len(organic_cards),
            skipped_partner=skipped_partner,
            ids_on_page=ids_on_page,
            has_more=has_more,
        )

    def _iter_cards(self, soup: BeautifulSoup) -> Iterable[Tag]:
        selectors = [
            "div.clsy-c-result-list-item",
            "article.clsy-c-result-list-item",
        ]
        for selector in selectors:
            cards = soup.select(selector)
            if cards:
                return cards
        return []

    def _is_partner_card(self, card: Tag) -> bool:
        if card.find(class_="clsy-c-result-list-item__partner"):
            return True
        text = card.get_text(" ", strip=True)
        return "partner-anzeige" in text.lower()

    def _extract_listing(self, card: Tag, fetched_at: datetime) -> Optional[Listing]:
        link = self._find_primary_link(card)
        if link is None:
            return None

        href = link.get("href")
        if not href:
            return None

        url = urljoin(self.BASE_URL, href)
        listing_id = self._extract_listing_id(url)
        if not listing_id:
            return None

        title = link.get_text(strip=True)

        price_text = self._extract_text(card, [
            ".clsy-c-result-list-item__price",
            "[data-testid='result-item-price']",
        ])
        price_value, price_currency = self._parse_price(price_text)

        location = self._extract_text(card, [
            ".clsy-c-result-list-item__location",
            ".clsy-c-result-list-item__meta-location",
            "[data-testid='result-item-location']",
        ])

        posted_text = self._extract_text(card, [
            ".clsy-c-result-list-item__time",
            "[data-testid='result-item-published']",
        ])
        posted_ts = self._parse_posted_ts(posted_text, fetched_at)

        image_url = None
        image = card.find("img")
        if image is not None:
            image_url = image.get("data-src") or image.get("src")
            if image_url:
                image_url = urljoin(self.BASE_URL, image_url)

        seller = self._extract_text(card, [
            ".clsy-c-result-list-item__seller",
            "[data-testid='result-item-seller']",
        ])

        return Listing(
            platform=self.PLATFORM,
            platform_id=listing_id,
            title=title,
            url=url,
            price_value=price_value,
            price_currency=price_currency,
            price_text=price_text.strip() if price_text else None,
            image_url=image_url,
            location=location.strip() if location else None,
            seller_name=seller.strip() if seller else None,
            posted_ts=posted_ts,
        )

    def _find_primary_link(self, card: Tag) -> Optional[Tag]:
        link = card.find("a", href=LISTING_ID_RE)
        if link:
            return link
        return card.find("a", href=True)

    def _extract_listing_id(self, url: str) -> Optional[str]:
        match = LISTING_ID_RE.search(url)
        if match:
            return match.group(1).lower()
        return None

    def _extract_text(self, card: Tag, selectors: Iterable[str]) -> Optional[str]:
        for selector in selectors:
            node = card.select_one(selector)
            if node and node.get_text(strip=True):
                return node.get_text(" ", strip=True)
        return None

    def _parse_price(self, price_text: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
        if not price_text:
            return None, None

        text = price_text.replace("\xa0", " ").strip()
        currency = None
        if "€" in text or "eur" in text.lower():
            currency = "EUR"

        normalized = re.sub(r"[^0-9,.-]", "", text)
        normalized = normalized.replace(".", "").replace(",", ".")
        try:
            value = float(normalized)
        except ValueError:
            value = None
        return value, currency

    def _parse_posted_ts(self, value: Optional[str], fetched_at: datetime) -> Optional[datetime]:
        if not value:
            return None

        text = value.strip()
        lower = text.lower()
        berlin_now = fetched_at.astimezone(BERLIN)

        if "heute" in lower:
            time_match = re.search(r"(\d{1,2}):(\d{2})", lower)
            if time_match:
                hour, minute = map(int, time_match.groups())
                dt = berlin_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                return to_utc_aware(dt)

        if "gestern" in lower:
            time_match = re.search(r"(\d{1,2}):(\d{2})", lower)
            if time_match:
                hour, minute = map(int, time_match.groups())
                dt = (berlin_now - timedelta(days=1)).replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                return to_utc_aware(dt)

        rel_match = RELATIVE_TIME_RE.search(lower)
        if rel_match:
            amount = int(rel_match.group("value"))
            unit = rel_match.group("unit")
            delta = None
            if unit.startswith("minute") or unit.startswith("min"):
                delta = timedelta(minutes=amount)
            elif unit.startswith("sek"):
                delta = timedelta(seconds=amount)
            elif unit.startswith("stunde"):
                delta = timedelta(hours=amount)
            elif unit.startswith("tag"):
                delta = timedelta(days=amount)
            elif unit.startswith("woche"):
                delta = timedelta(weeks=amount)
            if delta is not None:
                dt = berlin_now - delta
                return to_utc_aware(dt)

        abs_match = ABSOLUTE_DATE_RE.search(text)
        if abs_match:
            parts = abs_match.groupdict()
            year = int(parts.get("year") or berlin_now.year)
            day = int(parts["day"])
            month = int(parts["month"])
            hour = int(parts["hour"])
            minute = int(parts["minute"])
            dt = datetime(year, month, day, hour, minute, tzinfo=BERLIN)
            return to_utc_aware(dt)

        return None

    def _detect_has_more(self, soup: BeautifulSoup) -> bool:
        if soup.select_one("a[rel='next']"):
            return True
        if soup.select_one("li.clsy-c-pagination__item--next a"):
            return True
        if soup.select_one("button[aria-label='Weiter']"):
            return True
        return False

    def _detect_consent_block(self, soup: BeautifulSoup) -> bool:
        if soup.find(id="usercentrics-root"):
            return True
        if soup.select_one("div[data-testid='uc-banner']"):
            return True
        text = soup.get_text(" ", strip=True).lower()
        return "cookies" in text and "zustimmen" in text and not self._iter_cards(soup)
