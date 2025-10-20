"""Search provider for markt.de organic listings."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from datetime import datetime
from typing import Iterable, Optional
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from models import Listing, SearchResult
from providers.base import BaseProvider

logger = logging.getLogger(__name__)


LISTING_ID_RE = re.compile(r"/a/([0-9a-fA-F]{8})/")


class MarktDeProvider(BaseProvider):
    """Provider implementation that emits organic markt.de listings."""

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
        self.delay = float(os.environ.get("MARKTDE_DELAY_SEC", "0.35"))
        self.max_retries = int(os.environ.get("MARKTDE_MAX_RETRIES", "3"))
        self.resolver = "http"

    @property
    def platform_name(self) -> str:
        return self.PLATFORM

    def build_search_url(self, keyword: str, page: int = 1) -> str:
        """Construct a search URL respecting markt.de pagination rules."""

        slug = quote(keyword.strip(), safe="")
        path = f"/suche/{slug}/"
        if page > 1:
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
        """Return organic markt.de listings for the supplied keyword."""

        mode_label = mode or ("baseline" if crawl_all else "poll")
        metadata = {
            "platform": self.PLATFORM,
            "resolver": self.resolver,
            "consent_status": "not_detected",
            "mode": mode_label,
        }

        all_listings: list[Listing] = []
        seen_ids: set[str] = set()
        pages_scanned = 0
        has_more = False
        last_page_index: Optional[int] = None

        start_page = page_start if page_start and page_start > 0 else 1
        max_pages = None
        if max_pages_override is not None:
            max_pages = max(1, max_pages_override)
        elif not crawl_all:
            if poll_pages is not None and poll_pages > 0:
                max_pages = poll_pages
            else:
                max_pages = 1

        timeout = httpx.Timeout(self.timeout)
        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            page = start_page

            while True:
                if max_pages is not None and pages_scanned >= max_pages:
                    has_more = True
                    break

                url = self.build_search_url(keyword, page)

                response: Optional[httpx.Response] = None
                error_message: Optional[str] = None

                for attempt in range(1, self.max_retries + 1):
                    try:
                        logger.info(
                            {
                                "event": "md_request",
                                "platform": self.PLATFORM,
                                "url": url,
                                "attempt": attempt,
                                "mode": mode_label,
                                "page": page,
                                "requested_page": page,
                            }
                        )
                        response = await client.get(url)
                        break
                    except httpx.HTTPError as exc:  # pragma: no cover - network path
                        error_message = str(exc)
                        if attempt >= self.max_retries:
                            break
                        await asyncio.sleep(self.delay * (2 ** (attempt - 1)))

                if response is None:
                    metadata["error"] = error_message or "Unbekannter HTTP-Fehler"
                    metadata["status"] = "http_error"
                    break

                logger.info(
                    {
                        "event": "md_search",
                        "platform": self.PLATFORM,
                        "q": keyword,
                        "url": url,
                        "status": response.status_code,
                        "final_url": str(response.url),
                        "page": page,
                        "requested_page": page,
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

                page_data = self._extract_page_listings(soup)

                logger.info(
                    {
                        "event": "md_dom_counts",
                        "platform": self.PLATFORM,
                        "q": keyword,
                        "page": page,
                        "requested_page": page,
                        "a_total": page_data["anchor_total"],
                        "a_with_id": page_data["anchor_with_id"],
                        "partner_skipped": page_data["partner_skipped"],
                    }
                )

                new_items = []
                for listing in page_data["listings"]:
                    if listing.platform_id in seen_ids:
                        continue
                    seen_ids.add(listing.platform_id)
                    new_items.append(listing)

                logger.info(
                    {
                        "event": "md_page",
                        "platform": self.PLATFORM,
                        "q": keyword,
                        "page": page,
                        "requested_page": page,
                        "items_total": page_data["anchor_with_id"],
                        "items_promoted_skipped": page_data["partner_skipped"],
                        "items_kept": len(new_items),
                        "url": url,
                        "mode": mode_label,
                    }
                )

                pages_scanned += 1
                last_page_index = page

                if not new_items:
                    has_more = False
                    break

                all_listings.extend(new_items)

                if not page_data["maybe_has_more"]:
                    has_more = False
                    break

                page += 1
                await asyncio.sleep(self.delay + random.uniform(0, self.delay / 2))

        logger.info(
            {
                "event": "md_summary",
                "platform": self.PLATFORM,
                "q": keyword,
                "pages": pages_scanned,
                "items": len(all_listings),
                "has_more": has_more,
                "resolver": metadata.get("resolver"),
                "error": metadata.get("error"),
                "mode": mode_label,
            }
        )

        metadata.update(
            {
                "pages_scanned": pages_scanned,
                "last_page_index": last_page_index,
                "has_more": has_more,
            }
        )

        return SearchResult(
            items=all_listings,
            has_more=has_more,
            pages_scanned=pages_scanned,
            last_page_index=last_page_index,
            metadata=metadata,
        )

    def _extract_page_listings(self, soup: BeautifulSoup) -> dict:
        """Extract candidate listings from the supplied DOM."""

        listings: list[Listing] = []
        anchor_total = 0
        anchor_with_id = 0
        partner_skipped = 0
        seen_on_page: set[str] = set()

        for anchor in soup.find_all("a"):
            href = anchor.get("href")
            if not href:
                continue

            anchor_total += 1
            match = LISTING_ID_RE.search(href)
            if not match:
                continue
            anchor_with_id += 1

            listing_id = match.group(1).lower()
            if listing_id in seen_on_page:
                continue

            card = self._find_card_container(anchor)
            if card and self._is_partner_card(card):
                partner_skipped += 1
                continue

            listing = self._build_listing(anchor, card, listing_id)
            if listing is None:
                continue

            listings.append(listing)
            seen_on_page.add(listing_id)

        return {
            "listings": listings,
            "anchor_total": anchor_total,
            "anchor_with_id": anchor_with_id,
            "partner_skipped": partner_skipped,
            "maybe_has_more": self._detect_has_more(soup),
        }

    def _find_card_container(self, anchor: Tag) -> Optional[Tag]:
        node = anchor
        while node and node.name not in {"html", "body"}:
            if isinstance(node, Tag):
                classes = node.get("class", [])
                if any("result-list-item" in cls for cls in classes):
                    return node
                if node.name in {"article", "div", "li"} and node.get("data-testid"):
                    return node
            node = node.parent  # type: ignore[assignment]
        return anchor.parent if isinstance(anchor.parent, Tag) else None

    def _is_partner_card(self, card: Tag) -> bool:
        node: Optional[Tag] = card
        depth = 0
        while node is not None and depth < 4:
            if node.name in {"body", "html"}:
                break
            if node.find(class_="clsy-c-result-list-item__partner"):
                return True
            if any("partner-anzeige" in text.lower() for text in node.stripped_strings):
                return True
            parent = node.parent if isinstance(node.parent, Tag) else None
            node = parent
            depth += 1
        return False

    def _build_listing(self, anchor: Tag, card: Optional[Tag], listing_id: str) -> Optional[Listing]:
        title = anchor.get_text(" ", strip=True)
        if not title and card is not None:
            title = card.get_text(" ", strip=True)
        if not title:
            return None

        url = urljoin(self.BASE_URL, anchor.get("href"))

        price_text = self._extract_text(card, [
            "[data-testid='result-item-price']",
            ".clsy-c-result-list-item__price",
        ])
        price_value, price_currency = self._parse_price(price_text)

        location = self._extract_text(card, [
            "[data-testid='result-item-location']",
            ".clsy-c-result-list-item__location",
            ".clsy-c-result-list-item__meta-location",
        ])

        image_url = None
        if card is not None:
            image = card.find("img")
            if image is not None:
                image_url = image.get("data-src") or image.get("src")
                if image_url:
                    image_url = urljoin(self.BASE_URL, image_url)

        seller = self._extract_text(card, [
            "[data-testid='result-item-seller']",
            ".clsy-c-result-list-item__seller",
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
            posted_ts=None,
        )

    def _extract_text(self, card: Optional[Tag], selectors: Iterable[str]) -> Optional[str]:
        if card is None:
            return None
        for selector in selectors:
            node = card.select_one(selector)
            if node and node.get_text(strip=True):
                return node.get_text(" ", strip=True)
        return None

    def _parse_price(self, price_text: Optional[str]) -> tuple[Optional[float], Optional[str]]:
        if not price_text:
            return None, None

        normalized = price_text.replace("\xa0", " ").strip()
        currency = None
        if "€" in normalized or "eur" in normalized.lower():
            currency = "EUR"

        digits = re.sub(r"[^0-9,.-]", "", normalized)
        digits = digits.replace(".", "").replace(",", ".")
        try:
            value = float(digits)
        except ValueError:
            value = None
        return value, currency

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
        return False
