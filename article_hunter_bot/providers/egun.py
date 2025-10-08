import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

from models import Listing, SearchResult
from providers.base import BaseProvider

logger = logging.getLogger(__name__)


class EgunProvider(BaseProvider):
    """Production-grade provider implementation for egun.de"""

    BASE_URL = "https://www.egun.de/market/"
    SEARCH_URL = f"{BASE_URL}list_items.php"
    DETAIL_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Upgrade-Insecure-Requests": "1",
    }
    SEARCH_HEADERS = DETAIL_HEADERS
    BERLIN_TZ = ZoneInfo("Europe/Berlin")
    UTC_TZ = ZoneInfo("UTC")

    def __init__(self) -> None:
        self._session_headers = dict(self.SEARCH_HEADERS)

    @property
    def platform_name(self) -> str:
        return "egun.de"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def build_query(self, keyword: str) -> str:
        return keyword.strip()

    async def search(
        self,
        keyword: str,
        since_ts: Optional[datetime] = None,
        sample_mode: bool = False,
        crawl_all: bool = False,
        max_pages_override: Optional[int] = None,
    ) -> SearchResult:
        """Fetch listings for a keyword across all organic result pages."""

        query = self.build_query(keyword)
        all_items: List[Listing] = []
        seen_ids: set[str] = set()
        pages_scanned = 0
        total_count: Optional[int] = None
        has_more = False

        max_pages = max_pages_override or (2000 if crawl_all else (3 if sample_mode else 1))
        max_pages = min(max_pages, 2000)

        async with httpx.AsyncClient(
            headers=self._session_headers,
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            page = 1
            while page <= max_pages:
                page_items, page_total, page_has_more, soup, page_url = await self._fetch_page(
                    client, query, page
                )
                organic_count = len(page_items)
                duplicates_on_page = 0
                unique_items: List[Listing] = []

                for listing in page_items:
                    if listing.platform_id in seen_ids:
                        duplicates_on_page += 1
                        continue
                    seen_ids.add(listing.platform_id)
                    unique_items.append(listing)

                matched_items = len(unique_items)
                if organic_count > 0:
                    pages_scanned += 1

                if page_total is not None and total_count is None:
                    total_count = page_total

                if unique_items:
                    all_items.extend(unique_items)

                logger.info(
                    {
                        "event": "egun_page",
                        "q": keyword,
                        "page": page,
                        "items_on_page": organic_count,
                        "matched_items": matched_items,
                        "duplicates_on_page": duplicates_on_page,
                        "url": page_url,
                    }
                )

                has_more = page_has_more

                if not crawl_all and not sample_mode:
                    break

                if not page_has_more or organic_count == 0:
                    break

                page += 1
                await asyncio.sleep(0.35 if crawl_all else 0.55)

        return SearchResult(
            items=all_items,
            total_count=total_count,
            has_more=has_more,
            pages_scanned=pages_scanned,
            last_page_index=pages_scanned if pages_scanned > 0 else None,
        )

    # ------------------------------------------------------------------
    # Networking helpers
    # ------------------------------------------------------------------
    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        query: str,
        page: int,
    ) -> Tuple[List[Listing], Optional[int], bool, Optional[BeautifulSoup], str]:
        params = {
            "mode": "qry",
            "query": query,
            "plusdescr": "off",
            "wheremode": "and",
            "page": str(page),
        }

        response = await client.get(self.SEARCH_URL, params=params)
        response.raise_for_status()

        if response.encoding is None or "iso-8859-1" in response.headers.get("content-type", "").lower():
            response.encoding = "utf-8"

        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        items, total_count, has_more = self._parse_search_page(soup, query, page)

        return items, total_count, has_more, soup, str(response.url)

    def _parse_search_page(
        self,
        soup: BeautifulSoup,
        original_query: str,
        page: int,
    ) -> Tuple[List[Listing], Optional[int], bool]:
        listings: List[Listing] = []
        marker = self._locate_organic_marker(soup)

        logger.info(
            {
                "event": "egun_marker",
                "found": marker is not None,
                "note": "organic section located" if marker else "organic marker missing",
            }
        )

        if not marker:
            return listings, self._extract_total_count(soup), False

        seen_rows: set[int] = set()
        for link in marker.find_all_next("a", href=lambda x: x and "item.php?id=" in x):
            row = link.find_parent("tr")
            if not row:
                continue
            row_id = id(row)
            if row_id in seen_rows:
                continue
            seen_rows.add(row_id)
            listing = self._parse_single_listing(row, original_query)
            if listing:
                listings.append(listing)

        total_count = self._extract_total_count(soup)
        has_more = self._has_next_page(soup, page)
        return listings, total_count, has_more

    def _parse_single_listing(self, row, original_query: str) -> Optional[Listing]:
        link = row.find("a", href=lambda x: x and "item.php?id=" in x)
        if not link:
            return None

        href = link.get("href")
        title = link.get_text(" ", strip=True)
        if not href or not title:
            return None

        match = re.search(r"id=(\d+)", href)
        if not match:
            return None

        platform_id = match.group(1)
        normalized_title = unicodedata.normalize("NFKC", title).strip()
        normalized_title = normalized_title[:200]
        url = urljoin(self.BASE_URL, href)

        price_value, price_currency = self._extract_price(row)
        image_url = self._extract_image(row)

        return Listing(
            platform=self.platform_name,
            platform_id=platform_id,
            title=normalized_title,
            url=url,
            price_value=price_value,
            price_currency=price_currency,
            image_url=image_url,
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------
    def _locate_organic_marker(self, soup: BeautifulSoup):
        target_patterns = [
            "alle artikel, die",
            "titel oder beschreibung",
            "im titel enthalten",
        ]

        for element in soup.find_all(string=True):
            text = unicodedata.normalize("NFKC", element.strip()).casefold()
            if not text:
                continue
            if "alle artikel" in text and "titel" in text and "enthalten" in text:
                return element.parent if hasattr(element, "parent") else None
            if all(p in text for p in target_patterns[:2]):
                return element.parent if hasattr(element, "parent") else None
        return None

    def _extract_price(self, row) -> Tuple[Optional[float], Optional[str]]:
        price_candidates: List[str] = []
        for cell in row.find_all("td"):
            text = cell.get_text(" ", strip=True)
            if "€" in text or "eur" in text.lower():
                price_candidates.append(text)

        for candidate in price_candidates:
            value, currency = self._parse_price(candidate)
            if value is not None:
                return float(value), currency
        return None, "EUR"

    def _parse_price(self, raw: str) -> Tuple[Optional[Decimal], str]:
        text = raw.strip()
        currency = "EUR"
        if re.search(r"\$|usd", text, re.IGNORECASE):
            currency = "USD"
        elif re.search(r"£|gbp", text, re.IGNORECASE):
            currency = "GBP"

        cleaned = re.sub(r"[^\d.,]", "", text)
        if not cleaned:
            return None, currency

        decimal_match = re.search(r"[.,](\d{1,2})$", cleaned)
        try:
            if decimal_match:
                decimal_sep = cleaned[decimal_match.start()]
                integer_part = cleaned[: decimal_match.start()]
                decimal_part = decimal_match.group(1)
                thousands_sep = "," if decimal_sep == "." else "."
                integer_part = integer_part.replace(thousands_sep, "")
                normalized = f"{integer_part}.{decimal_part}"
            else:
                normalized = cleaned.replace(".", "").replace(",", "")
            return Decimal(normalized), currency
        except (InvalidOperation, ValueError):
            return None, currency

    def _extract_image(self, row) -> Optional[str]:
        img = row.find("img", src=True)
        if img:
            src = img.get("src")
            if src:
                return urljoin(self.BASE_URL, src)
        return None

    def _extract_total_count(self, soup: BeautifulSoup) -> Optional[int]:
        text = soup.get_text(" ", strip=True)
        patterns = [
            r"(\d+)\s+Treffer",
            r"(\d+)\s+Artikel",
            r"(\d+)\s+Ergebnisse",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return None

    def _has_next_page(self, soup: BeautifulSoup, current_page: int) -> bool:
        next_page = current_page + 1
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if f"page={next_page}" in href or f"start={(next_page-1)*50}" in href:
                return True
        return False

    # ------------------------------------------------------------------
    # Detail parsing for posted_ts
    # ------------------------------------------------------------------
    async def fetch_posted_ts_batch(self, listings: List[Listing], concurrency: int = 4) -> None:
        targets = [
            listing
            for listing in listings
            if listing.platform == self.platform_name and listing.posted_ts is None
        ]
        if not targets:
            return

        semaphore = asyncio.Semaphore(max(concurrency, 1))

        async with httpx.AsyncClient(
            headers=self.DETAIL_HEADERS,
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            async def worker(item: Listing) -> None:
                async with semaphore:
                    try:
                        await asyncio.sleep(0.3)
                        response = await client.get(item.url)
                        response.raise_for_status()
                        if response.encoding is None or "iso-8859-1" in response.headers.get(
                            "content-type", ""
                        ).lower():
                            response.encoding = "utf-8"
                        soup = BeautifulSoup(response.text, "html.parser")
                        posted_ts, mode = self._extract_posted_ts(soup)
                        if posted_ts:
                            item.posted_ts = posted_ts
                            logger.info(
                                {
                                    "event": "egun_posted_ts",
                                    "id": item.platform_id,
                                    "mode": mode,
                                    "posted_ts_utc": posted_ts.astimezone(self.UTC_TZ).isoformat(),
                                }
                            )
                    except Exception as exc:
                        logger.debug(f"Failed to fetch posted_ts for {item.url}: {exc}")

            await asyncio.gather(*(worker(item) for item in targets))

    def _extract_posted_ts(self, soup: BeautifulSoup) -> Tuple[Optional[datetime], Optional[str]]:
        direct = self._extract_direct_posted_ts(soup)
        if direct:
            return direct, "direct"

        computed = self._compute_posted_ts_from_duration(soup)
        if computed:
            return computed, "computed"

        return None, None

    def _extract_direct_posted_ts(self, soup: BeautifulSoup) -> Optional[datetime]:
        labels = [
            "Auktionsbeginn",
            "Eingestellt",
            "Angebotsbeginn",
            "Start",
            "Eingestellt am",
        ]
        for label in labels:
            node = self._find_detail_value(soup, label)
            if node:
                ts = self._parse_german_datetime(node)
                if ts:
                    return ts
        text = soup.get_text(" ", strip=True)
        return None

    def _compute_posted_ts_from_duration(self, soup: BeautifulSoup) -> Optional[datetime]:
        laufzeit_text = self._find_detail_value(soup, "Laufzeit")
        end_text = self._find_detail_value(soup, "vorauss. Ende")
        if not laufzeit_text or not end_text:
            return None

        laufzeit_match = re.search(r"(\d+)", laufzeit_text)
        if not laufzeit_match:
            return None
        days = int(laufzeit_match.group(1))
        end_dt = self._parse_german_datetime(end_text)
        if not end_dt:
            return None

        posted_local = end_dt - timedelta(days=days)
        return posted_local.astimezone(self.UTC_TZ)

    def _find_detail_value(self, soup: BeautifulSoup, label: str) -> Optional[str]:
        label_cf = unicodedata.normalize("NFKC", label).casefold()
        for dt in soup.find_all(["dt", "th"]):
            label_text = unicodedata.normalize("NFKC", dt.get_text(" ", strip=True)).casefold()
            if label_cf in label_text:
                sibling = dt.find_next("dd") or dt.find_next("td")
                if sibling:
                    return sibling.get_text(" ", strip=True)
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                label_text = unicodedata.normalize("NFKC", cells[0].get_text(" ", strip=True)).casefold()
                if label_cf in label_text:
                    return cells[1].get_text(" ", strip=True)
        return None

    def _parse_german_datetime(self, text: Optional[str]) -> Optional[datetime]:
        if not text:
            return None

        cleaned = unicodedata.normalize("NFKC", text)
        cleaned = cleaned.replace("Uhr", "").replace("\xa0", " ").strip()
        if "," in cleaned:
            parts = cleaned.split(",", 1)
            if len(parts) == 2 and re.search(r"\d", parts[1]):
                cleaned = parts[1].strip()
        cleaned = re.sub(r"\s+", " ", cleaned)

        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                dt_local = datetime.strptime(cleaned, fmt)
                dt_local = dt_local.replace(tzinfo=self.BERLIN_TZ)
                return dt_local.astimezone(self.UTC_TZ)
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------------
    # Keyword matching (title-only)
    # ------------------------------------------------------------------
    def matches_keyword(self, title: str, keyword: str) -> bool:
        norm_title = unicodedata.normalize("NFKC", title or "").casefold()
        norm_keyword = unicodedata.normalize("NFKC", keyword or "").casefold()
        tokens = [t for t in re.split(r"\s+", norm_keyword) if t]
        if not tokens:
            return False

        for token in tokens:
            pattern = rf"(?<![0-9a-zA-Zäöüß]){re.escape(token)}(?![0-9a-zA-Zäöüß])"
            if not re.search(pattern, norm_title):
                return False
            if token == "uhr" and re.search(r"\b\d{1,2}:\d{2}\s*uhr\b", norm_title):
                return False
        return True
